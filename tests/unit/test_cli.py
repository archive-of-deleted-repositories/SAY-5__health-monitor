"""Tests for argument parsing and a couple of end-to-end CLI invocations."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from health_monitor.cli import main, parse_duration


def test_parse_duration_known_units() -> None:
    assert parse_duration("30s") == timedelta(seconds=30)
    assert parse_duration("5m") == timedelta(minutes=5)
    assert parse_duration("2h") == timedelta(hours=2)
    assert parse_duration("1d") == timedelta(days=1)


def test_parse_duration_invalid() -> None:
    import click

    with pytest.raises(click.BadParameter):
        parse_duration("forever")


def _ensure_table(runner: CliRunner, tmp_path: Path) -> str:
    """Create a fresh sqlite db and return its URL."""
    db = tmp_path / "h.db"
    return f"sqlite:///{db}"


def test_add_endpoint_persists_and_status_lists_it(tmp_path: Path) -> None:
    runner = CliRunner()
    db_url = _ensure_table(runner, tmp_path)

    r = runner.invoke(
        main,
        [
            "add-endpoint",
            "--name",
            "api",
            "--url",
            "http://x/_",
            "--threshold",
            "3",
            "--recovery",
            "noop",
            "--db-url",
            db_url,
        ],
    )
    assert r.exit_code == 0, r.output
    assert "stored" in r.output

    r = runner.invoke(main, ["status", "--since", "1h", "--db-url", db_url, "--json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.output)
    assert any(row["name"] == "api" for row in rows)


def test_add_endpoint_bash_requires_script(tmp_path: Path) -> None:
    runner = CliRunner()
    db_url = _ensure_table(runner, tmp_path)
    r = runner.invoke(
        main,
        [
            "add-endpoint",
            "--name",
            "x",
            "--url",
            "http://x/_",
            "--recovery",
            "bash",
            "--db-url",
            db_url,
        ],
    )
    assert r.exit_code != 0
    assert "recovery-bash" in r.output


def test_recoveries_empty(tmp_path: Path) -> None:
    runner = CliRunner()
    db_url = _ensure_table(runner, tmp_path)
    r = runner.invoke(main, ["recoveries", "--since", "1h", "--db-url", db_url])
    assert r.exit_code == 0, r.output
    assert "no recoveries" in r.output


def test_simulate_fires_recovery_after_threshold(tmp_path: Path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "sim.db"
    db_url = f"sqlite:///{db_path}"
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(f"""
db_url: "{db_url}"
endpoints:
  - name: api
    url: "http://x/_"
    failure_threshold: 3
    check_interval_s: 1
    recovery:
      kind: noop
""")
    r = runner.invoke(
        main,
        ["simulate", "--config", str(cfg_path), "--endpoint", "api", "--failures", "5"],
    )
    assert r.exit_code == 0, r.output
    assert "recoveries fired: 1" in r.output

    # And the audit row should be visible via the recoveries CLI.
    r = runner.invoke(main, ["recoveries", "--since", "1h", "--db-url", db_url, "--json"])
    rows = json.loads(r.output)
    assert len(rows) == 1
    assert rows[0]["action_kind"] == "noop"


def test_simulate_unknown_endpoint(tmp_path: Path) -> None:
    runner = CliRunner()
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text("""
db_url: "sqlite:///t.db"
endpoints:
  - name: api
    url: "http://x/_"
""")
    r = runner.invoke(
        main,
        ["simulate", "--config", str(cfg_path), "--endpoint", "missing", "--failures", "3"],
    )
    assert r.exit_code != 0
