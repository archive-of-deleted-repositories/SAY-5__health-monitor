"""Tests for the dispatcher: builds the right hook and persists the audit row."""

from __future__ import annotations

from pathlib import Path

import pytest

from health_monitor.config import EndpointConfig, RecoveryConfig, RecoveryKind
from health_monitor.recovery import RecoveryDispatcher, build_hook
from health_monitor.store import EndpointKind as StoreKind
from health_monitor.store import Store


@pytest.fixture
def store() -> Store:
    s = Store(db_url="sqlite:///:memory:")
    s.create_all()
    return s


def test_build_hook_bash_requires_script() -> None:
    ep = EndpointConfig(
        name="x",
        url="http://x/_",
        recovery=RecoveryConfig(kind=RecoveryKind.BASH, script=None),
    )
    with pytest.raises(ValueError, match="bash recovery"):
        build_hook(ep)


def test_build_hook_systemctl_requires_unit() -> None:
    ep = EndpointConfig(
        name="x",
        url="http://x/_",
        recovery=RecoveryConfig(kind=RecoveryKind.SYSTEMCTL, unit=None),
    )
    with pytest.raises(ValueError, match="systemctl recovery"):
        build_hook(ep)


def test_build_hook_noop() -> None:
    ep = EndpointConfig(name="x", url="http://x/_")
    hook = build_hook(ep)
    assert hook.kind == "noop"


async def test_dispatcher_records_noop(store: Store) -> None:
    ep_id = store.upsert_endpoint(
        name="api",
        url="http://x/_",
        kind=StoreKind.HTTP,
        recovery_kind="noop",
        recovery_args={},
    )
    ep = EndpointConfig(name="api", url="http://x/_")
    d = RecoveryDispatcher(store=store)
    result = await d.dispatch(endpoint=ep, endpoint_id=ep_id)
    assert result.exit_code == 0
    rows = store.list_recoveries()
    assert len(rows) == 1
    assert rows[0]["endpoint"] == "api"
    assert rows[0]["action_kind"] == "noop"


async def test_dispatcher_records_bash_failure(store: Store, tmp_path: Path) -> None:
    script = tmp_path / "fail.sh"
    script.write_text("#!/usr/bin/env bash\necho oops >&2\nexit 9\n")
    script.chmod(0o755)
    ep_id = store.upsert_endpoint(
        name="api",
        url="http://x/_",
        kind=StoreKind.HTTP,
        recovery_kind="bash",
        recovery_args={"script": str(script)},
    )
    ep = EndpointConfig(
        name="api",
        url="http://x/_",
        recovery=RecoveryConfig(kind=RecoveryKind.BASH, script=str(script)),
    )
    d = RecoveryDispatcher(store=store)
    result = await d.dispatch(endpoint=ep, endpoint_id=ep_id)
    assert result.exit_code == 9
    rows = store.list_recoveries()
    assert rows[0]["exit_code"] == 9
    assert "oops" in rows[0]["stderr_excerpt"]


async def test_dispatcher_handles_hook_exception(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even if a hook raises, the dispatcher records an audit row."""

    class Boom:
        kind = "noop"

        async def run(self) -> None:
            raise RuntimeError("kaboom")

    def fake_build_hook(_ep):  # type: ignore[no-untyped-def]
        return Boom()

    monkeypatch.setattr("health_monitor.recovery.build_hook", fake_build_hook)

    ep_id = store.upsert_endpoint(
        name="x", url="http://x/_", kind=StoreKind.HTTP, recovery_kind="noop", recovery_args={}
    )
    ep = EndpointConfig(name="x", url="http://x/_")
    d = RecoveryDispatcher(store=store)
    result = await d.dispatch(endpoint=ep, endpoint_id=ep_id)
    assert result.exit_code == -1
    assert "kaboom" in result.stderr
    rows = store.list_recoveries()
    assert rows[0]["exit_code"] == -1
