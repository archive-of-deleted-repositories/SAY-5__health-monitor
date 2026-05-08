"""No-op recovery hook: useful for dry-run testing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import RecoveryResult


@dataclass
class NoopHook:
    """Records the firing without running anything."""

    label: str = ""
    kind: str = "noop"

    async def run(self) -> RecoveryResult:
        from . import RecoveryResult as _RR

        return _RR(
            action_kind=self.kind,
            action_args={"label": self.label},
            exit_code=0,
            stdout=f"noop fired for {self.label}",
            stderr="",
            duration_ms=0,
        )
