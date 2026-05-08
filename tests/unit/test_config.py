"""Tests for the YAML loader and Pydantic config models."""

from __future__ import annotations

from pathlib import Path

import pytest

from health_monitor.config import (
    EndpointConfig,
    EndpointKind,
    RecoveryConfig,
    RecoveryKind,
    load_config,
)


def test_load_config_round_trip(tmp_path: Path) -> None:
    body = """
db_url: "sqlite:///x.db"
endpoints:
  - name: api
    url: "http://localhost:8080/healthz"
    failure_threshold: 4
    recovery:
      kind: bash
      script: "scripts/x.sh"
      timeout_seconds: 30
"""
    p = tmp_path / "monitor.yaml"
    p.write_text(body)
    cfg = load_config(p)
    assert cfg.db_url == "sqlite:///x.db"
    assert len(cfg.endpoints) == 1
    ep = cfg.endpoints[0]
    assert ep.failure_threshold == 4
    assert ep.recovery.kind == RecoveryKind.BASH
    assert ep.recovery.script == "scripts/x.sh"
    assert ep.recovery.timeout_seconds == 30


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.yaml")


def test_endpoint_threshold_must_be_positive() -> None:
    with pytest.raises(ValueError):
        EndpointConfig(name="x", url="http://x/_", failure_threshold=0)


def test_recovery_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RecoveryConfig(kind=RecoveryKind.NOOP, timeout_seconds=0)


def test_endpoint_defaults() -> None:
    ep = EndpointConfig(name="x", url="http://x/_")
    assert ep.kind == EndpointKind.HTTP
    assert ep.failure_threshold == 3
    assert ep.recovery.kind == RecoveryKind.NOOP
