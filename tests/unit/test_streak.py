"""Unit tests for the failure-streak detector."""

from __future__ import annotations

import random

import pytest

from health_monitor.streak import StreakDetector


def test_unknown_endpoint_raises() -> None:
    d = StreakDetector(threshold_by_name={"a": 3})
    with pytest.raises(KeyError):
        d.observe("missing", passed=True)


def test_pass_keeps_counter_zero() -> None:
    d = StreakDetector(threshold_by_name={"a": 3})
    for _ in range(10):
        assert d.observe("a", passed=True) is False
        assert d.current("a") == 0


def test_threshold_one_fires_immediately() -> None:
    d = StreakDetector(threshold_by_name={"a": 1})
    assert d.observe("a", passed=False) is True


def test_three_consecutive_failures_fire_then_reset() -> None:
    d = StreakDetector(threshold_by_name={"a": 3})
    assert d.observe("a", passed=False) is False  # 1
    assert d.observe("a", passed=False) is False  # 2
    assert d.observe("a", passed=False) is True  # 3 -> fire + reset
    assert d.current("a") == 0
    # Need three more in a row to fire again
    assert d.observe("a", passed=False) is False  # 1
    assert d.observe("a", passed=False) is False  # 2
    assert d.observe("a", passed=False) is True  # 3 -> fire again


def test_pass_resets_streak() -> None:
    d = StreakDetector(threshold_by_name={"a": 3})
    d.observe("a", passed=False)
    d.observe("a", passed=False)
    d.observe("a", passed=True)  # reset
    assert d.current("a") == 0
    assert d.observe("a", passed=False) is False  # only 1 in a row now


def test_separate_endpoints_track_independently() -> None:
    d = StreakDetector(threshold_by_name={"a": 3, "b": 3})
    d.observe("a", passed=False)
    d.observe("a", passed=False)
    assert d.current("a") == 2
    assert d.current("b") == 0
    assert d.observe("b", passed=False) is False
    assert d.current("a") == 2  # untouched


def test_random_sequence_property() -> None:
    """Property: recovery fires iff a run of `threshold` consecutive failures
    just completed (since the last fire/pass)."""
    rng = random.Random(0xC0FFEE)
    threshold = 3
    d = StreakDetector(threshold_by_name={"a": threshold})

    streak = 0
    expected_fires = 0
    actual_fires = 0
    for _ in range(2_000):
        passed = rng.random() < 0.4
        if passed:
            streak = 0
            expect_fire = False
        else:
            streak += 1
            if streak == threshold:
                expect_fire = True
                streak = 0
            else:
                expect_fire = False
        if expect_fire:
            expected_fires += 1
        if d.observe("a", passed=passed):
            actual_fires += 1

    assert actual_fires == expected_fires


def test_reset_clears_counter() -> None:
    d = StreakDetector(threshold_by_name={"a": 3})
    d.observe("a", passed=False)
    d.observe("a", passed=False)
    d.reset("a")
    assert d.current("a") == 0
