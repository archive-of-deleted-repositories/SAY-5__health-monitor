"""Unit tests for HTTP and TCP probes."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from health_monitor.config import EndpointConfig, EndpointKind
from health_monitor.poller import poll_once, probe_http, probe_tcp
from health_monitor.store import CheckStatus


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (200, CheckStatus.PASS),
        (204, CheckStatus.PASS),
        (301, CheckStatus.FAIL),
        (404, CheckStatus.FAIL),
        (500, CheckStatus.FAIL),
        (503, CheckStatus.FAIL),
    ],
)
async def test_probe_http_status_classification(status_code: int, expected: CheckStatus) -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(status_code, text="x"))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await probe_http("https://example/_", timeout_s=2, client=client)
    assert result.status == expected
    assert result.latency_ms >= 0


async def test_probe_http_timeout_classified_as_timeout() -> None:
    def _raise(_req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("boom")

    transport = httpx.MockTransport(_raise)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await probe_http("https://example/_", timeout_s=2, client=client)
    assert result.status == CheckStatus.TIMEOUT
    assert result.error_text and "timeout" in result.error_text


async def test_probe_http_other_error_classified_as_error() -> None:
    def _raise(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    transport = httpx.MockTransport(_raise)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await probe_http("https://example/_", timeout_s=2, client=client)
    assert result.status == CheckStatus.ERROR


async def test_probe_tcp_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWriter:
        def close(self) -> None: ...
        async def wait_closed(self) -> None: ...

    async def fake_open(host: str, port: int) -> tuple[object, FakeWriter]:
        return object(), FakeWriter()

    monkeypatch.setattr("asyncio.open_connection", fake_open)
    result = await probe_tcp("tcp://localhost:1234", timeout_s=1)
    assert result.status == CheckStatus.PASS


async def test_probe_tcp_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_open(host: str, port: int) -> object:
        raise ConnectionRefusedError("nope")

    monkeypatch.setattr("asyncio.open_connection", fake_open)
    result = await probe_tcp("localhost:1234", timeout_s=1)
    assert result.status == CheckStatus.FAIL


async def test_probe_tcp_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_open(host: str, port: int) -> object:
        await asyncio.sleep(5)
        return object()

    monkeypatch.setattr("asyncio.open_connection", fake_open)
    result = await probe_tcp("localhost:1234", timeout_s=0.05)
    assert result.status == CheckStatus.TIMEOUT


async def test_probe_tcp_invalid_url() -> None:
    with pytest.raises(ValueError):
        await probe_tcp("notahostport", timeout_s=1)


async def test_poll_once_dispatches_by_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, int] = {"http": 0, "tcp": 0}

    async def fake_http(*_a, **_k):  # type: ignore[no-untyped-def]
        called["http"] += 1
        from health_monitor.poller import ProbeResult

        return ProbeResult(CheckStatus.PASS, 1)

    async def fake_tcp(*_a, **_k):  # type: ignore[no-untyped-def]
        called["tcp"] += 1
        from health_monitor.poller import ProbeResult

        return ProbeResult(CheckStatus.PASS, 1)

    monkeypatch.setattr("health_monitor.poller.probe_http", fake_http)
    monkeypatch.setattr("health_monitor.poller.probe_tcp", fake_tcp)

    http_ep = EndpointConfig(name="h", url="http://x/_", kind=EndpointKind.HTTP)
    tcp_ep = EndpointConfig(name="t", url="tcp://x:1", kind=EndpointKind.TCP)
    await poll_once(http_ep)
    await poll_once(tcp_ep)
    assert called == {"http": 1, "tcp": 1}
