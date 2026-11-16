"""Data trust pipeline for one sensor.

RAW --(bounded parse, schema, type, finite, unit, identity)--> NORMALIZED
NORMALIZED --(range, sequence, freshness, plausibility/spike, frozen)--> VALIDATED

Only verdicts with control_eligible=True may feed AUTO control.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError, field_validator

from orchid.core.config import PayloadLimits, SiteConfig
from orchid.core.jsonsafe import PayloadError, parse_json_bounded
from orchid.core.timeutil import parse_aware

MIN_DT_SECONDS = 0.5  # clamp for rate-of-change denominator


class TelemetryIn(BaseModel):
    """Strict device telemetry schema (schema_version 1.0)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    site_id: str = Field(pattern=r"^site\d{2}$")
    device_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    device_ts: str
    seq: StrictInt = Field(ge=0, le=2**63 - 1)
    metric: Literal["temperature", "humidity"]
    value: StrictFloat | StrictInt
    unit: str = Field(max_length=8)

    @field_validator("device_ts")
    @classmethod
    def _aware(cls, v: str) -> str:
        parse_aware(v)
        return v


@dataclass(frozen=True)
class NormalizedSample:
    site_id: str
    device_id: str
    metric: str
    value: float
    unit: str
    device_ts: datetime
    seq: int
    gateway_ingest_ts: datetime


@dataclass(frozen=True)
class Verdict:
    stage: Literal["REJECTED", "NORMALIZED", "VALIDATED"]
    result: str
    quality: Literal["GOOD", "SUSPECT", "BAD", "STALE"]
    control_eligible: bool
    sample: NormalizedSample | None = None
    detail: str = ""


def _reject(result: str, detail: str = "", quality: str = "BAD") -> Verdict:
    return Verdict("REJECTED", result, quality, False, None, detail)  # type: ignore[arg-type]


@dataclass
class _State:
    last_seq: int | None = None
    base_value: float | None = None
    base_ts: datetime | None = None
    pending: list[NormalizedSample] = field(default_factory=list)
    frozen_value: float | None = None
    frozen_since: datetime | None = None
    frozen_count: int = 0


class SensorValidator:
    """Stateful validator for a single (site, device, metric)."""

    def __init__(self, site: SiteConfig, limits: PayloadLimits, device_id: str, metric: str) -> None:
        if site.sensors.get(device_id) != metric:
            raise ValueError(f"{device_id} is not the configured {metric} sensor")
        self.site = site
        self.limits = limits
        self.device_id = device_id
        self.metric = metric
        self.contract = site.sensor_contract[metric]
        self.max_rate = site.max_delta_per_second[metric]
        self._s = _State()

    # ---------------- RAW -> NORMALIZED ----------------
    def _normalize(self, t_site: str, t_dev: str, t_metric: str, raw: bytes, now: datetime) -> NormalizedSample | Verdict:
        try:
            obj = parse_json_bounded(raw, max_bytes=self.limits.max_telemetry_bytes, max_depth=self.limits.max_json_depth)
        except PayloadError as exc:
            return _reject(exc.code, exc.detail)
        if not isinstance(obj, dict):
            return _reject("SCHEMA_INVALID", "payload must be a JSON object")
        try:
            msg = TelemetryIn.model_validate(obj)
        except ValidationError as exc:
            return _reject("SCHEMA_INVALID", exc.errors()[0].get("loc", ("?",))[0].__str__())
        value = float(msg.value)
        if not math.isfinite(value):
            return _reject("NON_FINITE")
        identity = {
            (msg.site_id, t_site, self.site.site_id),
            (msg.device_id, t_dev, self.device_id),
            (msg.metric, t_metric, self.metric),
        }
        if any(len(set(triple)) != 1 for triple in identity):
            return _reject("IDENTITY_MISMATCH", "payload/topic/registration disagree")
        if msg.unit != self.contract.unit:
            return _reject("UNIT_INVALID", f"expected {self.contract.unit}")
        return NormalizedSample(
            site_id=msg.site_id,
            device_id=msg.device_id,
            metric=msg.metric,
            value=value,
            unit=msg.unit,
            device_ts=parse_aware(msg.device_ts),
            seq=msg.seq,
            gateway_ingest_ts=now,
        )

    # ---------------- NORMALIZED -> VALIDATED ----------------
    def process(self, t_site: str, t_dev: str, t_metric: str, raw: bytes, now: datetime) -> Verdict:
        norm = self._normalize(t_site, t_dev, t_metric, raw, now)
        if isinstance(norm, Verdict):
            return norm
        s = self._s
        if not self.contract.min <= norm.value <= self.contract.max:
            return Verdict("NORMALIZED", "RANGE_FAIL", "BAD", False, norm, "outside sensor capability range")
        if norm.device_ts > now + timedelta(seconds=self.site.future_skew_seconds):
            return Verdict("NORMALIZED", "TIMESTAMP_FUTURE", "BAD", False, norm)
        if s.last_seq is not None and norm.seq <= s.last_seq:
            return Verdict("NORMALIZED", "REPLAY_OR_DUPLICATE", "BAD", False, norm, f"seq {norm.seq} <= {s.last_seq}")
        s.last_seq = norm.seq
        age = (now - norm.device_ts).total_seconds()
        if age > self.site.stale_seconds:
            return Verdict("NORMALIZED", "STALE", "STALE", False, norm, f"age {age:.1f}s")
        spike = self._plausibility(norm)
        if spike is not None:
            return spike
        if self._frozen(norm):
            return Verdict("NORMALIZED", "FROZEN", "SUSPECT", False, norm, "signal unchanged")
        return Verdict("VALIDATED", "VALIDATED", "GOOD", True, norm)

    def _rate(self, a_val: float, a_ts: datetime, b_val: float, b_ts: datetime) -> float:
        dt = max(abs((b_ts - a_ts).total_seconds()), MIN_DT_SECONDS)
        return abs(b_val - a_val) / dt

    def _plausibility(self, norm: NormalizedSample) -> Verdict | None:
        s = self._s
        if s.base_value is None or s.base_ts is None:
            self._rebase(norm)
            return None
        if self._rate(s.base_value, s.base_ts, norm.value, norm.device_ts) <= self.max_rate:
            s.pending.clear()
            self._rebase(norm)
            return None
        # Implausible jump: hold as pending spike until confirmed by consistent samples.
        if s.pending:
            last = s.pending[-1]
            if self._rate(last.value, last.device_ts, norm.value, norm.device_ts) > self.max_rate:
                s.pending.clear()
        s.pending.append(norm)
        if len(s.pending) >= self.site.spike_confirm_samples:
            s.pending.clear()
            self._rebase(norm)
            return None  # confirmed step change -> validated
        return Verdict("NORMALIZED", "SPIKE_PENDING", "SUSPECT", False, norm, "PLAUSIBILITY_FAIL rate-of-change")

    def _rebase(self, norm: NormalizedSample) -> None:
        self._s.base_value = norm.value
        self._s.base_ts = norm.device_ts

    def _frozen(self, norm: NormalizedSample) -> bool:
        s = self._s
        if s.frozen_value is None or norm.value != s.frozen_value:
            s.frozen_value = norm.value
            s.frozen_since = norm.device_ts
            s.frozen_count = 1
            return False
        s.frozen_count += 1
        assert s.frozen_since is not None
        span = (norm.device_ts - s.frozen_since).total_seconds()
        return span >= self.site.frozen_seconds and s.frozen_count >= self.site.frozen_min_samples
