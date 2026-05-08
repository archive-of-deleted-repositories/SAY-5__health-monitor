"""Tests for the SQLAlchemy store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from health_monitor.store import CheckStatus, EndpointKind, Store, truncate


@pytest.fixture
def store() -> Store:
    s = Store(db_url="sqlite:///:memory:")
    s.create_all()
    return s


def test_upsert_endpoint_inserts_then_updates(store: Store) -> None:
    ep_id = store.upsert_endpoint(name="a", url="http://x/_")
    again = store.upsert_endpoint(name="a", url="http://y/_", failure_threshold=5)
    assert ep_id == again
    row = store.get_endpoint("a")
    assert row is not None
    assert row["url"] == "http://y/_"
    assert row["failure_threshold"] == 5


def test_list_endpoints_only_active(store: Store) -> None:
    store.upsert_endpoint(name="a", url="http://x/_", is_active=True)
    store.upsert_endpoint(name="b", url="http://x/_", is_active=False)
    active = store.list_endpoints(only_active=True)
    assert {e["name"] for e in active} == {"a"}
    all_eps = store.list_endpoints(only_active=False)
    assert {e["name"] for e in all_eps} == {"a", "b"}


def test_record_and_summarise(store: Store) -> None:
    ep_id = store.upsert_endpoint(name="a", url="http://x/_")
    for status, lat in [
        (CheckStatus.PASS, 10),
        (CheckStatus.PASS, 20),
        (CheckStatus.FAIL, 30),
        (CheckStatus.PASS, 40),
    ]:
        store.record_check(endpoint_id=ep_id, status=status, latency_ms=lat)
    rows = store.status_summary(since=timedelta(hours=1))
    assert len(rows) == 1
    assert rows[0]["total_checks"] == 4
    assert rows[0]["passed"] == 3
    assert rows[0]["uptime_pct"] == 75.0
    assert rows[0]["p95_latency_ms"] is not None


def test_summary_excludes_old_checks(store: Store) -> None:
    ep_id = store.upsert_endpoint(name="a", url="http://x/_")
    store.record_check(
        endpoint_id=ep_id,
        status=CheckStatus.PASS,
        latency_ms=5,
        recorded_at=datetime.now(UTC) - timedelta(days=2),
    )
    rows = store.status_summary(since=timedelta(hours=1))
    assert rows[0]["total_checks"] == 0


def test_record_recovery_persists_args_and_excerpts(store: Store) -> None:
    ep_id = store.upsert_endpoint(name="a", url="http://x/_")
    store.record_recovery(
        endpoint_id=ep_id,
        action_kind="bash",
        action_args={"script": "x.sh", "args": ["1", "2"]},
        exit_code=3,
        stdout_excerpt="hello",
        stderr_excerpt="bye",
        duration_ms=42,
    )
    rows = store.list_recoveries(since=timedelta(hours=1))
    assert len(rows) == 1
    assert rows[0]["action_kind"] == "bash"
    assert rows[0]["action_args"] == {"script": "x.sh", "args": ["1", "2"]}
    assert rows[0]["exit_code"] == 3
    assert rows[0]["duration_ms"] == 42


def test_count_recoveries(store: Store) -> None:
    ep_id = store.upsert_endpoint(name="a", url="http://x/_")
    assert store.count_recoveries() == 0
    store.record_recovery(
        endpoint_id=ep_id,
        action_kind="noop",
        action_args={},
        exit_code=0,
        stdout_excerpt="",
        stderr_excerpt="",
        duration_ms=0,
    )
    assert store.count_recoveries() == 1


def test_truncate_handles_long_text() -> None:
    out = truncate("a" * 10_000, limit=100)
    assert out.startswith("a" * 100)
    assert "truncated" in out
    assert truncate(None) == ""
    assert truncate(b"hi") == "hi"


def test_endpoint_kind_enum_round_trip(store: Store) -> None:
    ep_id = store.upsert_endpoint(name="t", url="tcp://x:1", kind=EndpointKind.TCP)
    row = store.get_endpoint("t")
    assert row is not None
    assert row["kind"] == "tcp"
    assert row["id"] == ep_id
