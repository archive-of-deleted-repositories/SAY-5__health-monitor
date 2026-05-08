"""Bash recovery hook: invokes a script via subprocess."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import RecoveryResult


@dataclass
class BashHook:
    """Run a script with optional args via ``subprocess`` (async)."""

    script: str
    args: list[str] = field(default_factory=list)
    timeout_seconds: int = 60
    kind: str = "bash"

    async def run(self) -> RecoveryResult:
        from . import RecoveryResult as _RR

        if not os.path.exists(self.script):
            return _RR(
                action_kind=self.kind,
                action_args={"script": self.script, "args": list(self.args)},
                exit_code=127,
                stdout="",
                stderr=f"script not found: {self.script}",
                duration_ms=0,
            )
        if not os.access(self.script, os.X_OK):
            return _RR(
                action_kind=self.kind,
                action_args={"script": self.script, "args": list(self.args)},
                exit_code=126,
                stdout="",
                stderr=f"script not executable: {self.script}",
                duration_ms=0,
            )

        started = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                self.script,
                *self.args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return _RR(
                action_kind=self.kind,
                action_args={"script": self.script, "args": list(self.args)},
                exit_code=126,
                stdout="",
                stderr=f"spawn failed: {exc!s}",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):  # pragma: no cover - cleanup best effort
                await proc.wait()
            return _RR(
                action_kind=self.kind,
                action_args={"script": self.script, "args": list(self.args)},
                exit_code=124,
                stdout="",
                stderr=f"timeout after {self.timeout_seconds}s",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        elapsed = int((time.perf_counter() - started) * 1000)
        return _RR(
            action_kind=self.kind,
            action_args={"script": self.script, "args": list(self.args)},
            exit_code=int(proc.returncode or 0),
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            duration_ms=elapsed,
        )
