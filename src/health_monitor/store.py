"""SQLAlchemy-backed persistence for endpoints, health checks, and recovery actions."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


class EndpointKind(StrEnum):
    HTTP = "http"
    TCP = "tcp"


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    ERROR = "error"


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Endpoint(Base):
    __tablename__ = "endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[EndpointKind] = mapped_column(
        Enum(EndpointKind, name="endpoint_kind"), nullable=False, default=EndpointKind.HTTP
    )
    failure_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    check_interval_s: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    recovery_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="noop")
    recovery_args: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    checks: Mapped[list[HealthCheck]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )
    recoveries: Mapped[list[RecoveryAction]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )


class HealthCheck(Base):
    __tablename__ = "health_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    status: Mapped[CheckStatus] = mapped_column(
        Enum(CheckStatus, name="check_status"), nullable=False
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    endpoint: Mapped[Endpoint] = relationship(back_populates="checks")

    __table_args__ = (Index("ix_health_checks_endpoint_recorded", "endpoint_id", "recorded_at"),)


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False
    )
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    action_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    action_args: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stdout_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    endpoint: Mapped[Endpoint] = relationship(back_populates="recoveries")

    __table_args__ = (Index("ix_recovery_actions_endpoint_fired", "endpoint_id", "fired_at"),)


# ---------------------------------------------------------------------------
# Engine / session helpers
# ---------------------------------------------------------------------------


class Store:
    """Thin wrapper around an SQLAlchemy engine + session factory."""

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        connect_args: dict[str, Any] = {}
        if db_url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
        self.engine = create_engine(db_url, future=True, connect_args=connect_args)
        self._session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False, future=True
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        sess = self._session_factory()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    # -------- endpoint helpers --------

    def upsert_endpoint(
        self,
        *,
        name: str,
        url: str,
        kind: EndpointKind = EndpointKind.HTTP,
        failure_threshold: int = 3,
        check_interval_s: int = 10,
        timeout_s: int = 5,
        recovery_kind: str = "noop",
        recovery_args: dict[str, Any] | None = None,
        is_active: bool = True,
    ) -> int:
        """Insert or update an endpoint by name; returns its id."""
        with self.session() as s:
            existing = s.execute(select(Endpoint).where(Endpoint.name == name)).scalar_one_or_none()
            if existing is None:
                ep = Endpoint(
                    name=name,
                    url=url,
                    kind=kind,
                    failure_threshold=failure_threshold,
                    check_interval_s=check_interval_s,
                    timeout_s=timeout_s,
                    recovery_kind=recovery_kind,
                    recovery_args=recovery_args or {},
                    is_active=is_active,
                )
                s.add(ep)
                s.flush()
                return int(ep.id)
            existing.url = url
            existing.kind = kind
            existing.failure_threshold = failure_threshold
            existing.check_interval_s = check_interval_s
            existing.timeout_s = timeout_s
            existing.recovery_kind = recovery_kind
            existing.recovery_args = recovery_args or {}
            existing.is_active = is_active
            return int(existing.id)

    def list_endpoints(self, only_active: bool = True) -> list[dict[str, Any]]:
        with self.session() as s:
            stmt = select(Endpoint)
            if only_active:
                stmt = stmt.where(Endpoint.is_active.is_(True))
            rows = s.execute(stmt).scalars().all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "url": r.url,
                    "kind": r.kind.value,
                    "failure_threshold": r.failure_threshold,
                    "check_interval_s": r.check_interval_s,
                    "timeout_s": r.timeout_s,
                    "recovery_kind": r.recovery_kind,
                    "recovery_args": dict(r.recovery_args),
                    "is_active": r.is_active,
                }
                for r in rows
            ]

    def get_endpoint(self, name: str) -> dict[str, Any] | None:
        with self.session() as s:
            r = s.execute(select(Endpoint).where(Endpoint.name == name)).scalar_one_or_none()
            if r is None:
                return None
            return {
                "id": r.id,
                "name": r.name,
                "url": r.url,
                "kind": r.kind.value,
                "failure_threshold": r.failure_threshold,
                "check_interval_s": r.check_interval_s,
                "timeout_s": r.timeout_s,
                "recovery_kind": r.recovery_kind,
                "recovery_args": dict(r.recovery_args),
                "is_active": r.is_active,
            }

    # -------- writes --------

    def record_check(
        self,
        *,
        endpoint_id: int,
        status: CheckStatus,
        latency_ms: int | None,
        error_text: str | None = None,
        recorded_at: datetime | None = None,
    ) -> int:
        with self.session() as s:
            hc = HealthCheck(
                endpoint_id=endpoint_id,
                status=status,
                latency_ms=latency_ms,
                error_text=error_text,
                recorded_at=recorded_at or _utcnow(),
            )
            s.add(hc)
            s.flush()
            return int(hc.id)

    def record_recovery(
        self,
        *,
        endpoint_id: int,
        action_kind: str,
        action_args: dict[str, Any],
        exit_code: int,
        stdout_excerpt: str,
        stderr_excerpt: str,
        duration_ms: int,
        fired_at: datetime | None = None,
    ) -> int:
        with self.session() as s:
            ra = RecoveryAction(
                endpoint_id=endpoint_id,
                action_kind=action_kind,
                action_args=action_args,
                exit_code=exit_code,
                stdout_excerpt=stdout_excerpt,
                stderr_excerpt=stderr_excerpt,
                duration_ms=duration_ms,
                fired_at=fired_at or _utcnow(),
            )
            s.add(ra)
            s.flush()
            return int(ra.id)

    # -------- reads --------

    def status_summary(self, since: timedelta = timedelta(hours=24)) -> list[dict[str, Any]]:
        """Compute uptime% and approximate p95 latency per endpoint over a window."""
        cutoff = _utcnow() - since
        with self.session() as s:
            endpoints = s.execute(select(Endpoint)).scalars().all()
            out: list[dict[str, Any]] = []
            for ep in endpoints:
                checks = (
                    s.execute(
                        select(HealthCheck)
                        .where(HealthCheck.endpoint_id == ep.id)
                        .where(HealthCheck.recorded_at >= cutoff)
                    )
                    .scalars()
                    .all()
                )
                total = len(checks)
                passed = sum(1 for c in checks if c.status == CheckStatus.PASS)
                uptime_pct = (passed / total * 100.0) if total else 0.0
                latencies = sorted(c.latency_ms for c in checks if c.latency_ms is not None)
                p95 = _percentile(latencies, 95) if latencies else None
                out.append(
                    {
                        "name": ep.name,
                        "total_checks": total,
                        "passed": passed,
                        "uptime_pct": round(uptime_pct, 2),
                        "p95_latency_ms": p95,
                    }
                )
            return out

    def list_recoveries(self, since: timedelta = timedelta(hours=24)) -> list[dict[str, Any]]:
        cutoff = _utcnow() - since
        with self.session() as s:
            rows = s.execute(
                select(RecoveryAction, Endpoint)
                .join(Endpoint, RecoveryAction.endpoint_id == Endpoint.id)
                .where(RecoveryAction.fired_at >= cutoff)
                .order_by(RecoveryAction.fired_at.desc())
            ).all()
            out: list[dict[str, Any]] = []
            for ra, ep in rows:
                out.append(
                    {
                        "endpoint": ep.name,
                        "fired_at": ra.fired_at.isoformat(),
                        "action_kind": ra.action_kind,
                        "action_args": dict(ra.action_args),
                        "exit_code": ra.exit_code,
                        "duration_ms": ra.duration_ms,
                        "stdout_excerpt": ra.stdout_excerpt,
                        "stderr_excerpt": ra.stderr_excerpt,
                    }
                )
            return out

    def count_recoveries(self) -> int:
        with self.session() as s:
            return int(s.execute(select(func.count(RecoveryAction.id))).scalar_one())


def _percentile(sorted_values: list[int], pct: float) -> int:
    """Compute a percentile using nearest-rank on a pre-sorted list."""
    if not sorted_values:
        return 0
    k = max(0, min(len(sorted_values) - 1, int(round(pct / 100.0 * len(sorted_values))) - 1))
    return int(sorted_values[k])


def truncate(text: str | bytes | None, limit: int = 4096) -> str:
    """Decode and truncate stdout/stderr to a safe size."""
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[...truncated {len(text) - limit} bytes]"


def dumps_args(args: dict[str, Any]) -> str:
    """Stable JSON encoding for action_args (used by tests)."""
    return json.dumps(args, sort_keys=True, default=str)
