"""Click CLI for the health monitor."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import timedelta

import click

from .config import (
    EndpointConfig,
    EndpointKind,
    RecoveryConfig,
    RecoveryKind,
    load_config,
)
from .obs.logs import configure_logging, get_logger
from .recovery import RecoveryDispatcher
from .store import CheckStatus, Store
from .store import EndpointKind as StoreEndpointKind
from .streak import StreakDetector

log = get_logger(__name__)


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)


def parse_duration(s: str) -> timedelta:
    """Parse durations like ``30s``, ``5m``, ``2h``, ``1d``."""
    m = _DURATION_RE.match(s)
    if not m:
        raise click.BadParameter(f"invalid duration {s!r}; use e.g. 30s, 5m, 2h, 1d")
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "s":
        return timedelta(seconds=n)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    return timedelta(days=n)


@click.group()
@click.option("--db-url", envvar="HEALTH_MONITOR_DB_URL", default=None)
@click.option("--log-level", envvar="HEALTH_MONITOR_LOG_LEVEL", default="INFO")
@click.option("--log-json/--log-text", default=False)
@click.pass_context
def main(ctx: click.Context, db_url: str | None, log_level: str, log_json: bool) -> None:
    """Service health monitor: poll endpoints, fire recovery hooks, audit everything."""
    configure_logging(level=log_level, json=log_json)
    ctx.ensure_object(dict)
    ctx.obj["db_url"] = db_url
    ctx.obj["log_level"] = log_level
    ctx.obj["log_json"] = log_json


@main.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
@click.option("--duration", default=None, help="Stop after this duration (e.g. 30s).")
@click.pass_context
def run(ctx: click.Context, config_path: str, duration: str | None) -> None:
    """Run the polling loop until SIGTERM/SIGINT."""
    cfg = load_config(config_path)
    db_url = ctx.obj.get("db_url") or cfg.db_url
    store = Store(db_url=db_url)
    store.create_all()

    streak = StreakDetector(
        threshold_by_name={ep.name: ep.failure_threshold for ep in cfg.endpoints}
    )
    dispatcher = RecoveryDispatcher(store=store)
    duration_s = parse_duration(duration).total_seconds() if duration else None

    from .poller import run_loop

    asyncio.run(
        run_loop(
            endpoints=cfg.endpoints,
            store=store,
            streak=streak,
            dispatcher=dispatcher,
            duration_s=duration_s,
        )
    )


@main.command()
@click.option("--since", default="24h", help="Time window (e.g. 30s, 5m, 24h).")
@click.option("--db-url", envvar="HEALTH_MONITOR_DB_URL", default="sqlite:///health_monitor.db")
@click.option("--json", "as_json", is_flag=True, help="Output JSON instead of a table.")
def status(since: str, db_url: str, as_json: bool) -> None:
    """Show uptime% and p95 latency per endpoint."""
    store = Store(db_url=db_url)
    store.create_all()
    rows = store.status_summary(since=parse_duration(since))
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo("(no endpoints)")
        return
    click.echo(f"{'endpoint':<24}{'checks':>8}{'pass':>6}{'uptime%':>10}{'p95 ms':>10}")
    click.echo("-" * 58)
    for r in rows:
        p95 = r["p95_latency_ms"]
        click.echo(
            f"{r['name']:<24}{r['total_checks']:>8}"
            f"{r['passed']:>6}{r['uptime_pct']:>10}"
            f"{(p95 if p95 is not None else '-'):>10}"
        )


@main.command()
@click.option("--since", default="24h")
@click.option("--db-url", envvar="HEALTH_MONITOR_DB_URL", default="sqlite:///health_monitor.db")
@click.option("--json", "as_json", is_flag=True)
def recoveries(since: str, db_url: str, as_json: bool) -> None:
    """List recovery actions in the given window."""
    store = Store(db_url=db_url)
    store.create_all()
    rows = store.list_recoveries(since=parse_duration(since))
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo("(no recoveries)")
        return
    click.echo(f"{'fired_at':<28}{'endpoint':<20}{'kind':<12}{'exit':>6}{'ms':>8}")
    click.echo("-" * 74)
    for r in rows:
        click.echo(
            f"{r['fired_at']:<28}{r['endpoint']:<20}"
            f"{r['action_kind']:<12}{r['exit_code']:>6}{r['duration_ms']:>8}"
        )


@main.command(name="add-endpoint")
@click.option("--name", required=True)
@click.option("--url", required=True)
@click.option(
    "--kind", type=click.Choice([k.value for k in EndpointKind]), default=EndpointKind.HTTP.value
)
@click.option("--threshold", type=int, default=3)
@click.option("--interval", type=int, default=10)
@click.option("--timeout", type=int, default=5)
@click.option(
    "--recovery",
    "recovery_kind",
    type=click.Choice([k.value for k in RecoveryKind]),
    default=RecoveryKind.NOOP.value,
)
@click.option("--recovery-bash", default=None, help="Path to a bash recovery script.")
@click.option("--recovery-unit", default=None, help="systemd unit for systemctl recovery.")
@click.option("--db-url", envvar="HEALTH_MONITOR_DB_URL", default="sqlite:///health_monitor.db")
def add_endpoint(
    name: str,
    url: str,
    kind: str,
    threshold: int,
    interval: int,
    timeout: int,
    recovery_kind: str,
    recovery_bash: str | None,
    recovery_unit: str | None,
    db_url: str,
) -> None:
    """Insert or update an endpoint row in the database."""
    store = Store(db_url=db_url)
    store.create_all()

    rec_args: dict[str, object] = {"timeout_seconds": 60}
    rk = RecoveryKind(recovery_kind)
    if rk == RecoveryKind.BASH:
        if not recovery_bash:
            raise click.BadParameter("--recovery-bash is required when --recovery=bash")
        rec_args["script"] = recovery_bash
    elif rk == RecoveryKind.SYSTEMCTL:
        if not recovery_unit:
            raise click.BadParameter("--recovery-unit is required when --recovery=systemctl")
        rec_args["unit"] = recovery_unit

    ep_id = store.upsert_endpoint(
        name=name,
        url=url,
        kind=StoreEndpointKind(kind),
        failure_threshold=threshold,
        check_interval_s=interval,
        timeout_s=timeout,
        recovery_kind=rk.value,
        recovery_args=rec_args,
    )
    click.echo(f"endpoint {name!r} stored (id={ep_id})")


@main.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
@click.option("--endpoint", "endpoint_name", required=True)
@click.option("--failures", type=int, default=3, help="Number of synthetic failures to inject.")
def simulate(config_path: str, endpoint_name: str, failures: int) -> None:
    """Inject synthetic failures into the streak detector to test the recovery path.

    This does NOT actually take any service down; it feeds fake failures
    into the detector and dispatches the recovery hook if the threshold
    is reached. Useful for verifying restart scripts in dry-run.
    """
    cfg = load_config(config_path)
    target = next((e for e in cfg.endpoints if e.name == endpoint_name), None)
    if target is None:
        raise click.UsageError(f"endpoint {endpoint_name!r} not in config")

    store = Store(db_url=cfg.db_url)
    store.create_all()
    ep_id = store.upsert_endpoint(
        name=target.name,
        url=target.url,
        kind=StoreEndpointKind(target.kind.value),
        failure_threshold=target.failure_threshold,
        check_interval_s=target.check_interval_s,
        timeout_s=target.timeout_s,
        recovery_kind=target.recovery.kind.value,
        recovery_args={
            "script": target.recovery.script,
            "args": target.recovery.args,
            "unit": target.recovery.unit,
            "timeout_seconds": target.recovery.timeout_seconds,
        },
    )

    streak = StreakDetector(threshold_by_name={target.name: target.failure_threshold})
    dispatcher = RecoveryDispatcher(store=store)
    fired = 0

    async def _drive() -> None:
        nonlocal fired
        for i in range(failures):
            triggered = streak.observe(target.name, passed=False)
            click.echo(f"  failure {i + 1}/{failures} streak={streak.current(target.name)}")
            if triggered:
                fired += 1
                click.echo(f"  -> recovery firing for {target.name}")
                store.record_check(
                    endpoint_id=ep_id,
                    status=CheckStatus.FAIL,
                    latency_ms=0,
                    error_text="simulated",
                )
                await dispatcher.dispatch(endpoint=target, endpoint_id=ep_id)

    asyncio.run(_drive())
    click.echo(f"done. recoveries fired: {fired}")


# Build a thin shim used by setup-tools' console_script entry point.
def _entry_point() -> None:  # pragma: no cover
    main(prog_name="health-monitor")


if __name__ == "__main__":  # pragma: no cover
    main(prog_name="health-monitor")


# Helper exposed to integration tests.
def make_dispatcher(store: Store) -> RecoveryDispatcher:
    return RecoveryDispatcher(store=store)


# Re-export for tests
__all__ = [
    "EndpointConfig",
    "EndpointKind",
    "RecoveryConfig",
    "RecoveryKind",
    "main",
    "make_dispatcher",
    "parse_duration",
]


# Allow `python -m health_monitor.cli` invocation
if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
