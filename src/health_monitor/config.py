"""Configuration: Pydantic models + YAML loader."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EndpointKind(StrEnum):
    HTTP = "http"
    TCP = "tcp"


class RecoveryKind(StrEnum):
    BASH = "bash"
    SYSTEMCTL = "systemctl"
    NOOP = "noop"


class RecoveryConfig(BaseModel):
    kind: RecoveryKind = RecoveryKind.NOOP
    script: str | None = None
    args: list[str] = Field(default_factory=list)
    unit: str | None = None
    timeout_seconds: int = 60

    @field_validator("timeout_seconds")
    @classmethod
    def _positive_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("timeout_seconds must be positive")
        return v


class EndpointConfig(BaseModel):
    name: str
    url: str
    kind: EndpointKind = EndpointKind.HTTP
    failure_threshold: int = 3
    check_interval_s: int = 10
    timeout_s: int = 5
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)
    is_active: bool = True

    @field_validator("failure_threshold")
    @classmethod
    def _threshold_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("failure_threshold must be >= 1")
        return v

    @field_validator("check_interval_s", "timeout_s")
    @classmethod
    def _interval_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("interval/timeout must be positive")
        return v


class MonitorConfig(BaseModel):
    endpoints: list[EndpointConfig]
    db_url: str = "sqlite:///health_monitor.db"
    log_level: str = "INFO"
    log_json: bool = False


class AppSettings(BaseSettings):
    """Process-wide settings (env-overridable)."""

    model_config = SettingsConfigDict(
        env_prefix="HEALTH_MONITOR_",
        env_file=".env",
        extra="ignore",
    )
    db_url: str = "sqlite:///health_monitor.db"
    log_level: str = "INFO"
    log_json: bool = False


def load_config(path: str | Path) -> MonitorConfig:
    """Load monitor configuration from a YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("r", encoding="utf-8") as fp:
        raw: dict[str, Any] = yaml.safe_load(fp) or {}
    return MonitorConfig.model_validate(raw)
