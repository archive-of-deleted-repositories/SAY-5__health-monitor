"""Recovery hook protocol and dispatcher."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from ..config import EndpointConfig, RecoveryKind
from ..obs.logs import get_logger
from ..store import Store, truncate
from .bash import BashHook
from .noop import NoopHook
from .systemctl import SystemctlHook

log = get_logger(__name__)


@dataclass(frozen=True)
class RecoveryResult:
    """Outcome of running one recovery hook."""

    action_kind: str
    action_args: dict[str, object]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class RecoveryHook(Protocol):
    """A recovery hook executes side effects to restore a failing service."""

    kind: str

    async def run(self) -> RecoveryResult:  # pragma: no cover - protocol stub
        ...


def build_hook(endpoint: EndpointConfig) -> RecoveryHook:
    """Construct the right hook for an endpoint's recovery config."""
    rc = endpoint.recovery
    if rc.kind == RecoveryKind.BASH:
        if not rc.script:
            raise ValueError(f"bash recovery for {endpoint.name!r} requires `script`")
        return BashHook(script=rc.script, args=list(rc.args), timeout_seconds=rc.timeout_seconds)
    if rc.kind == RecoveryKind.SYSTEMCTL:
        if not rc.unit:
            raise ValueError(f"systemctl recovery for {endpoint.name!r} requires `unit`")
        return SystemctlHook(unit=rc.unit, timeout_seconds=rc.timeout_seconds)
    if rc.kind == RecoveryKind.NOOP:
        return NoopHook(label=endpoint.name)
    raise ValueError(f"unsupported recovery kind: {rc.kind}")  # pragma: no cover


class RecoveryDispatcher:
    """Runs the right hook for an endpoint and persists the audit row."""

    def __init__(self, store: Store) -> None:
        self.store = store

    async def dispatch(self, *, endpoint: EndpointConfig, endpoint_id: int) -> RecoveryResult:
        hook = build_hook(endpoint)
        started = time.perf_counter()
        try:
            result = await hook.run()
        except Exception as exc:  # noqa: BLE001 - want to record any failure
            elapsed = int((time.perf_counter() - started) * 1000)
            err_text = f"hook raised: {exc!s}"
            log.error("recovery.hook_error", endpoint=endpoint.name, error=err_text)
            self.store.record_recovery(
                endpoint_id=endpoint_id,
                action_kind=endpoint.recovery.kind.value,
                action_args={
                    "script": endpoint.recovery.script,
                    "args": endpoint.recovery.args,
                    "unit": endpoint.recovery.unit,
                    "timeout_seconds": endpoint.recovery.timeout_seconds,
                },
                exit_code=-1,
                stdout_excerpt="",
                stderr_excerpt=truncate(err_text),
                duration_ms=elapsed,
            )
            return RecoveryResult(
                action_kind=endpoint.recovery.kind.value,
                action_args={},
                exit_code=-1,
                stdout="",
                stderr=err_text,
                duration_ms=elapsed,
            )

        self.store.record_recovery(
            endpoint_id=endpoint_id,
            action_kind=result.action_kind,
            action_args=dict(result.action_args),
            exit_code=result.exit_code,
            stdout_excerpt=truncate(result.stdout),
            stderr_excerpt=truncate(result.stderr),
            duration_ms=result.duration_ms,
        )
        log.info(
            "recovery.recorded",
            endpoint=endpoint.name,
            kind=result.action_kind,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
        )
        return result


__all__ = [
    "BashHook",
    "NoopHook",
    "RecoveryDispatcher",
    "RecoveryHook",
    "RecoveryResult",
    "SystemctlHook",
    "build_hook",
]
