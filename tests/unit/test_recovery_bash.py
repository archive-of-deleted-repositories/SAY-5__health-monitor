"""Unit tests for the bash recovery hook."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from health_monitor.recovery.bash import BashHook
from health_monitor.recovery.noop import NoopHook
from health_monitor.recovery.systemctl import SystemctlHook


def _write_script(tmp_path: Path, body: str, executable: bool = True) -> Path:
    p = tmp_path / "script.sh"
    p.write_text(f"#!/usr/bin/env bash\n{body}\n")
    if executable:
        st = p.stat()
        p.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


async def test_bash_success_propagates_exit_code(tmp_path: Path) -> None:
    script = _write_script(tmp_path, "echo hello; exit 0")
    hook = BashHook(script=str(script))
    result = await hook.run()
    assert result.exit_code == 0
    assert "hello" in result.stdout


async def test_bash_nonzero_exit_propagates(tmp_path: Path) -> None:
    script = _write_script(tmp_path, "echo broken >&2; exit 7")
    hook = BashHook(script=str(script))
    result = await hook.run()
    assert result.exit_code == 7
    assert "broken" in result.stderr


async def test_bash_args_passed(tmp_path: Path) -> None:
    script = _write_script(tmp_path, 'echo "$1-$2"')
    hook = BashHook(script=str(script), args=["x", "y"])
    result = await hook.run()
    assert result.exit_code == 0
    assert "x-y" in result.stdout


async def test_bash_missing_script_returns_127(tmp_path: Path) -> None:
    hook = BashHook(script=str(tmp_path / "nope.sh"))
    result = await hook.run()
    assert result.exit_code == 127
    assert "not found" in result.stderr


async def test_bash_non_executable_script_returns_126(tmp_path: Path) -> None:
    script = _write_script(tmp_path, "echo hi", executable=False)
    hook = BashHook(script=str(script))
    result = await hook.run()
    assert result.exit_code == 126
    assert "not executable" in result.stderr


async def test_bash_timeout_kills_process(tmp_path: Path) -> None:
    script = _write_script(tmp_path, "sleep 5; echo done")
    hook = BashHook(script=str(script), timeout_seconds=1)
    result = await hook.run()
    assert result.exit_code == 124
    assert "timeout" in result.stderr


async def test_noop_hook_records_label() -> None:
    hook = NoopHook(label="api")
    result = await hook.run()
    assert result.exit_code == 0
    assert "api" in result.stdout
    assert result.action_kind == "noop"


async def test_systemctl_on_macos_or_missing_returns_126(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    hook = SystemctlHook(unit="x.service")
    result = await hook.run()
    assert result.exit_code == 126
    assert "systemctl" in result.stderr
