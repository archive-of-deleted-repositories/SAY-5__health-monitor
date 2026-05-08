"""Per-endpoint consecutive-failure counter.

Behavior:
    * pass -> reset counter to 0
    * fail/timeout/error -> increment counter
    * when counter == threshold, fire recovery and reset counter to 0

The detector is purely in-memory; it intentionally does not persist counters.
On process restart, the streak resets, which is the safer default for
recovery (don't fire stale actions on startup).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


@dataclass
class StreakDetector:
    """Tracks consecutive failures per endpoint name."""

    threshold_by_name: dict[str, int]
    counters: dict[str, int] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def observe(self, endpoint_name: str, passed: bool) -> bool:
        """Record one observation. Returns True iff recovery should fire."""
        with self._lock:
            if endpoint_name not in self.threshold_by_name:
                raise KeyError(f"unknown endpoint: {endpoint_name}")
            if passed:
                self.counters[endpoint_name] = 0
                return False
            new_count = self.counters.get(endpoint_name, 0) + 1
            threshold = self.threshold_by_name[endpoint_name]
            if new_count >= threshold:
                self.counters[endpoint_name] = 0
                return True
            self.counters[endpoint_name] = new_count
            return False

    def current(self, endpoint_name: str) -> int:
        with self._lock:
            return self.counters.get(endpoint_name, 0)

    def reset(self, endpoint_name: str) -> None:
        with self._lock:
            self.counters[endpoint_name] = 0
