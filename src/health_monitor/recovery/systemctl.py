"""systemctl recovery hook (Linux). On macOS, fails fast with a clear message."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import RecoveryResult


@dataclass
class SystemctlHook:
    """Wraps ``systemctl restart <unit>``.

    On macOS the binary is absent; the hook records a clear error rather
    than crashing the polling loop. See docs/recovery-hooks.md for the
    launchctl alternative.
    """

    unit: str
    timeout_seconds: int = 60
    kind: str = "systemctl"

    async def run(self) -> RecoveryResult:
        from . import RecoveryResult as _RR

        if sys.platform == "darwin" or shutil.which("systemctl") is None:
            return _RR(
                action_kind=self.kind,
                action_args={"unit": self.unit},
                exit_code=126,
                stdout="",
                stderr=(
                    "systemctl unavailable on this platform; "
                    "use launchctl on macOS or run on Linux."
                ),
                duration_ms=0,
            )

        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "restart",
            self.unit,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):  # pragma: no cover - cleanup
                await proc.wait()
            return _RR(
                action_kind=self.kind,
                action_args={"unit": self.unit},
                exit_code=124,
                stdout="",
                stderr=f"timeout after {self.timeout_seconds}s",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        elapsed = int((time.perf_counter() - started) * 1000)
        return _RR(
            action_kind=self.kind,
            action_args={"unit": self.unit},
            exit_code=int(proc.returncode or 0),
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            duration_ms=elapsed,
        )
