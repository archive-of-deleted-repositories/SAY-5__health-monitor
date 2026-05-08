"""Async HTTP and TCP probes plus the main polling loop."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from .config import EndpointConfig, EndpointKind
from .obs.logs import get_logger
from .store import CheckStatus, Store

if TYPE_CHECKING:
    from .recovery import RecoveryDispatcher
    from .streak import StreakDetector

log = get_logger(__name__)


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single probe attempt."""

    status: CheckStatus
    latency_ms: int
    error_text: str | None = None


async def probe_http(
    url: str, timeout_s: float, *, client: httpx.AsyncClient | None = None
) -> ProbeResult:
    """Issue a GET against ``url`` and classify the result."""
    start = time.perf_counter()
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)
    assert client is not None
    try:
        try:
            resp = await client.get(url)
        except httpx.TimeoutException as exc:
            elapsed = int((time.perf_counter() - start) * 1000)
            return ProbeResult(CheckStatus.TIMEOUT, elapsed, f"timeout: {exc!s}")
        except httpx.HTTPError as exc:
            elapsed = int((time.perf_counter() - start) * 1000)
            return ProbeResult(CheckStatus.ERROR, elapsed, f"http error: {exc!s}")
        except Exception as exc:  # pragma: no cover - defensive
            elapsed = int((time.perf_counter() - start) * 1000)
            return ProbeResult(CheckStatus.ERROR, elapsed, f"unexpected: {exc!s}")
        elapsed = int((time.perf_counter() - start) * 1000)
        if 200 <= resp.status_code < 300:
            return ProbeResult(CheckStatus.PASS, elapsed)
        return ProbeResult(CheckStatus.FAIL, elapsed, f"http {resp.status_code}")
    finally:
        if own_client:
            await client.aclose()


async def probe_tcp(url_or_hostport: str, timeout_s: float) -> ProbeResult:
    """Open a TCP connection. Accepts ``host:port`` or ``tcp://host:port``."""
    host, port = _parse_host_port(url_or_hostport)
    start = time.perf_counter()
    try:
        fut = asyncio.open_connection(host=host, port=port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout_s)
    except TimeoutError:
        elapsed = int((time.perf_counter() - start) * 1000)
        return ProbeResult(CheckStatus.TIMEOUT, elapsed, "tcp timeout")
    except ConnectionRefusedError as exc:
        elapsed = int((time.perf_counter() - start) * 1000)
        return ProbeResult(CheckStatus.FAIL, elapsed, f"refused: {exc!s}")
    except OSError as exc:
        elapsed = int((time.perf_counter() - start) * 1000)
        return ProbeResult(CheckStatus.ERROR, elapsed, f"os error: {exc!s}")
    elapsed = int((time.perf_counter() - start) * 1000)
    writer.close()
    with contextlib.suppress(Exception):  # pragma: no cover - cleanup best effort
        await writer.wait_closed()
    del reader
    return ProbeResult(CheckStatus.PASS, elapsed)


def _parse_host_port(raw: str) -> tuple[str, int]:
    if "://" in raw:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
        port = parsed.port or 0
    else:
        if ":" not in raw:
            raise ValueError(f"tcp endpoint must be host:port, got {raw!r}")
        host, _, port_s = raw.rpartition(":")
        port = int(port_s)
    if not host or port <= 0:
        raise ValueError(f"invalid tcp host/port from {raw!r}")
    return host, port


async def poll_once(
    endpoint: EndpointConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> ProbeResult:
    """Run a single probe based on the endpoint kind."""
    if endpoint.kind == EndpointKind.HTTP:
        return await probe_http(endpoint.url, endpoint.timeout_s, client=client)
    if endpoint.kind == EndpointKind.TCP:
        return await probe_tcp(endpoint.url, endpoint.timeout_s)
    raise ValueError(f"unsupported endpoint kind: {endpoint.kind}")  # pragma: no cover


async def _endpoint_loop(
    endpoint: EndpointConfig,
    endpoint_id: int,
    store: Store,
    streak: StreakDetector,
    dispatcher: RecoveryDispatcher,
    stop: asyncio.Event,
    *,
    client: httpx.AsyncClient,
) -> None:
    """Per-endpoint polling task."""
    while not stop.is_set():
        result = await poll_once(endpoint, client=client)
        store.record_check(
            endpoint_id=endpoint_id,
            status=result.status,
            latency_ms=result.latency_ms,
            error_text=result.error_text,
        )
        log.info(
            "probe",
            endpoint=endpoint.name,
            status=result.status.value,
            latency_ms=result.latency_ms,
            error=result.error_text,
        )
        triggered = streak.observe(endpoint.name, result.status == CheckStatus.PASS)
        if triggered:
            log.warning(
                "recovery.fire",
                endpoint=endpoint.name,
                threshold=endpoint.failure_threshold,
            )
            await dispatcher.dispatch(endpoint=endpoint, endpoint_id=endpoint_id)

        try:
            await asyncio.wait_for(stop.wait(), timeout=endpoint.check_interval_s)
        except TimeoutError:
            continue


async def run_loop(
    endpoints: list[EndpointConfig],
    store: Store,
    streak: StreakDetector,
    dispatcher: RecoveryDispatcher,
    *,
    duration_s: float | None = None,
) -> None:
    """Run polling tasks until SIGTERM/SIGINT or until ``duration_s`` elapses."""
    if not endpoints:
        log.warning("run_loop.no_endpoints")
        return

    # Persist endpoint rows so checks can reference them.
    name_to_id: dict[str, int] = {}
    for ep in endpoints:
        ep_id = store.upsert_endpoint(
            name=ep.name,
            url=ep.url,
            kind=__import__("health_monitor.store", fromlist=["EndpointKind"]).EndpointKind(
                ep.kind.value
            ),
            failure_threshold=ep.failure_threshold,
            check_interval_s=ep.check_interval_s,
            timeout_s=ep.timeout_s,
            recovery_kind=ep.recovery.kind.value,
            recovery_args={
                "script": ep.recovery.script,
                "args": ep.recovery.args,
                "unit": ep.recovery.unit,
                "timeout_seconds": ep.recovery.timeout_seconds,
            },
            is_active=ep.is_active,
        )
        name_to_id[ep.name] = ep_id

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop(*_: object) -> None:
        log.info("shutdown.signal_received")
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # pragma: no cover - non-unix
            loop.add_signal_handler(sig, _request_stop)

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        tasks = [
            asyncio.create_task(
                _endpoint_loop(
                    endpoint=ep,
                    endpoint_id=name_to_id[ep.name],
                    store=store,
                    streak=streak,
                    dispatcher=dispatcher,
                    stop=stop,
                    client=client,
                ),
                name=f"poll-{ep.name}",
            )
            for ep in endpoints
            if ep.is_active
        ]

        if duration_s is not None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=duration_s)
            except TimeoutError:
                stop.set()
        else:
            await stop.wait()

        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        log.info("shutdown.complete")
