"""Typed, validated loader for config/platform.yaml.

All thresholds come from configuration. Values flagged `lab_assumption` are
lab assumptions, never real hardware specifications.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

METRICS = ("temperature", "humidity")
GROUPS = ("fan_group", "dehumidifier_group")
MAX_SESSION_EXPIRY = 24 * 3600  # never "infinite" (0xFFFFFFFF)
OnOff = Literal["ON", "OFF"]


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Timing(_Frozen):
    telemetry_interval_seconds: float = Field(gt=0)
    tick_seconds: float = Field(gt=0)
    heartbeat_interval_seconds: float = Field(gt=0)
    state_republish_seconds: float = Field(gt=0)
    heartbeat_stale_seconds: float = Field(gt=0)
    state_stale_seconds: float = Field(gt=0)
    telemetry_stale_seconds: float = Field(gt=0)


class CommandCfg(_Frozen):
    default_ttl_seconds: int = Field(gt=0)
    max_ttl_seconds: int = Field(gt=0)
    dedup_cache_size: int = Field(gt=0, le=100_000)
    retry_backoff_seconds: float = Field(gt=0)


class Sessions(_Frozen):
    controller_session_expiry_seconds: int = Field(ge=0, le=MAX_SESSION_EXPIRY)
    historian_session_expiry_seconds: int = Field(ge=0, le=MAX_SESSION_EXPIRY)
    api_session_expiry_seconds: int = Field(ge=0, le=MAX_SESSION_EXPIRY)
    bridge_session_expiry_seconds: int = Field(ge=0, le=MAX_SESSION_EXPIRY)
    broker_persistent_client_expiration: str


class PayloadLimits(_Frozen):
    max_telemetry_bytes: int = Field(gt=0, le=65536)
    max_command_bytes: int = Field(gt=0, le=65536)
    max_event_bytes: int = Field(gt=0, le=65536)
    max_json_depth: int = Field(gt=0, le=16)


class WebCfg(_Frozen):
    jwt_ttl_seconds: int = Field(gt=0, le=3600)
    jwt_algorithm: Literal["HS256"]
    login_rate_limit_per_minute: int = Field(gt=0)


class RateLimitCfg(_Frozen):
    normal_per_second: float = Field(gt=0)
    normal_burst: int = Field(gt=0)
    critical_per_second: float = Field(gt=0)
    critical_burst: int = Field(gt=0)
    server_per_second: float = Field(default=5, gt=0)
    server_burst: int = Field(default=50, gt=0)


class QueueCfg(_Frozen):
    normal_max: int = Field(gt=0)
    critical_max: int = Field(gt=0)


class AuditCfg(_Frozen):
    max_detail_string_len: int = Field(gt=0, le=4096)
    max_detail_keys: int = Field(gt=0, le=64)
    rate_limit: RateLimitCfg
    queue: QueueCfg


class HistorianCfg(_Frozen):
    raw_retention_rows: int = Field(gt=0)
    gateway_retention_rows: int = Field(gt=0)
    events_retention_rows: int = Field(gt=0)
    commands_retention_rows: int = Field(default=20000, gt=0)
    late_threshold_seconds: float = Field(gt=0)


class PlatformConfig(_Frozen):
    name: str
    architecture_version: str
    mqtt_version: Literal[5]
    topic_root: Literal["orchid/v1"]
    timing: Timing
    command: CommandCfg
    sessions: Sessions
    payload_limits: PayloadLimits
    web: WebCfg
    audit: AuditCfg
    historian: HistorianCfg


class SensorContract(_Frozen):
    unit: str
    min: float
    max: float
    lab_assumption: bool
    note: str = ""

    @model_validator(mode="after")
    def _range(self) -> "SensorContract":
        if not self.min < self.max:
            raise ValueError("sensor contract min must be < max")
        return self


class Confirmation(_Frozen):
    n: int = Field(gt=0)
    m: int = Field(gt=0)

    @model_validator(mode="after")
    def _nm(self) -> "Confirmation":
        if self.n > self.m:
            raise ValueError("confirmation n must be <= m")
        return self


class Interlock(_Frozen):
    temperature_high: float
    lab_assumption: bool


class SiteConfig(_Frozen):
    site_id: str = Field(pattern=r"^site\d{2}$")
    display_name: str = Field(min_length=1, max_length=64)
    target_temperature: float
    target_humidity: float
    sensor_contract: dict[str, SensorContract]
    stale_seconds: float = Field(gt=0)
    future_skew_seconds: float = Field(ge=0)
    frozen_seconds: float = Field(gt=0)
    frozen_min_samples: int = Field(gt=1)
    max_delta_per_second: dict[str, float]
    spike_confirm_samples: int = Field(gt=1)
    confirmation: Confirmation
    hysteresis: dict[str, float]
    manual_override_ttl_seconds: int = Field(gt=0)
    maintenance_ttl_seconds: int = Field(default=1800, gt=0, le=86400)
    failsafe_after_seconds: float = Field(gt=0)
    failsafe_outputs: dict[str, OnOff]
    maintenance_outputs: dict[str, OnOff] = Field(
        default_factory=lambda: {"fan_group": "OFF", "dehumidifier_group": "OFF"}
    )
    interlock: Interlock
    sensors: dict[str, str]
    actuator_groups: dict[str, list[str]]

    @model_validator(mode="after")
    def _consistency(self) -> "SiteConfig":
        for name in (self.sensor_contract, self.max_delta_per_second, self.hysteresis):
            if set(name) != set(METRICS):
                raise ValueError(f"metric keys must be exactly {METRICS}")
        if any(v <= 0 for v in self.hysteresis.values()):
            raise ValueError("hysteresis must be > 0")
        if any(v <= 0 for v in self.max_delta_per_second.values()):
            raise ValueError("max_delta_per_second must be > 0")
        for outputs in (self.failsafe_outputs, self.maintenance_outputs):
            if set(outputs) != set(GROUPS):
                raise ValueError(f"outputs must define exactly {GROUPS}")
        if set(self.actuator_groups) != set(GROUPS):
            raise ValueError(f"actuator_groups must be exactly {GROUPS}")
        if any(len(m) != 2 for m in self.actuator_groups.values()):
            raise ValueError("each actuator group must contain exactly two members")
        if sorted(self.sensors.values()) != sorted(METRICS):
            raise ValueError("exactly one sensor per metric is required")
        hi, _ = self.thresholds("temperature")
        if not hi < self.interlock.temperature_high <= self.sensor_contract["temperature"].max:
            raise ValueError("interlock must be above control threshold and within sensor range")
        return self

    def thresholds(self, metric: str) -> tuple[float, float]:
        """Return (high, low) operational thresholds for hysteresis control."""
        target = self.target_temperature if metric == "temperature" else self.target_humidity
        half = self.hysteresis[metric] / 2.0
        return (round(target + half, 6), round(target - half, 6))

    def sensor_for(self, metric: str) -> str:
        return next(dev for dev, m in self.sensors.items() if m == metric)

    def all_actuators(self) -> list[str]:
        return [m for group in GROUPS for m in self.actuator_groups[group]]


class Config(_Frozen):
    platform: PlatformConfig
    sites: dict[str, SiteConfig]
    source: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)

    def raw_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.source)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config_dict(raw: dict[str, Any], site_overrides: dict[str, dict] | None = None) -> Config:
    try:
        defaults = raw.get("site_defaults", {})
        sites: dict[str, SiteConfig] = {}
        for site_id, site_raw in (raw.get("sites") or {}).items():
            merged = _deep_merge(defaults, site_raw or {})
            merged = _deep_merge(merged, (site_overrides or {}).get(site_id, {}))
            sites[site_id] = SiteConfig(site_id=site_id, **merged)
        if not sites:
            raise ConfigError("no sites configured")
        return Config(platform=PlatformConfig(**raw["platform"]), sites=sites, source=copy.deepcopy(raw))
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"malformed config: {exc}") from exc


def default_config_path() -> Path:
    env = os.environ.get("ORCHID_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "config" / "platform.yaml"


def load_config(path: Path | str | None = None) -> Config:
    path = Path(path) if path else default_config_path()
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    overrides: dict[str, dict] = {}
    sites_dir = path.parent / "sites"
    if sites_dir.is_dir():
        for file in sorted(sites_dir.glob("site*.yaml")):
            with open(file, encoding="utf-8") as fh:
                overrides[file.stem] = yaml.safe_load(fh) or {}
    return load_config_dict(raw, overrides)
