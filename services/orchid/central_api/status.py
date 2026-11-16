"""Freshness-aware display semantics (pure functions).

A retained `ON` is never displayed as "currently ON" unless:
  observed_at is fresh AND the site heartbeat is fresh AND LWT/bridge say online.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from orchid.core.timeutil import parse_aware

FUTURE_SKEW_S = 5.0  # a timestamp further in the future than this is untrustworthy, never "fresh"


def is_fresh(age: float | None, stale_s: float) -> bool:
    return age is not None and -FUTURE_SKEW_S <= age <= stale_s


def age_seconds(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        return round((now - parse_aware(ts)).total_seconds(), 1)
    except ValueError:
        return None


def heartbeat_of(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row or not row.get("heartbeat"):
        return None
    try:
        hb = json.loads(row["heartbeat"])
        return hb if isinstance(hb, dict) else None
    except ValueError:
        return None


def site_status(row: dict[str, Any] | None, *, now: datetime, stale_s: float) -> str:
    """ONLINE | DEGRADED | OFFLINE | UNKNOWN."""
    if row is None:
        return "UNKNOWN"
    if row.get("bridge_state") == "0":
        return "OFFLINE"  # central broker published the bridge's LWT: uplink lost
    hb = heartbeat_of(row)
    if hb is None:
        return "UNKNOWN"
    if hb.get("status") == "OFFLINE":
        return "OFFLINE"  # controller LWT or graceful shutdown
    age = age_seconds(hb.get("ts") or row.get("hb_ts"), now)
    if not is_fresh(age, stale_s):
        return "UNKNOWN"  # cannot claim online with an old (or future-dated) heartbeat
    return "DEGRADED" if hb.get("health") == "DEGRADED" else "ONLINE"


def actuator_view(row: dict[str, Any] | None, *, site_status: str, now: datetime, stale_s: float) -> dict[str, Any]:
    if row is None:
        return {"state": None, "display": "UNKNOWN", "fresh": False, "observed_at": None, "quality": None,
                "age_s": None}
    age = age_seconds(row.get("observed_at"), now)
    fresh = site_status in ("ONLINE", "DEGRADED") and is_fresh(age, stale_s) and row.get("state") in ("ON", "OFF")
    display = row["state"] if fresh else "STALE"
    return {"state": row.get("state"), "display": display, "fresh": fresh, "observed_at": row.get("observed_at"),
            "quality": row.get("quality"), "commanded": row.get("commanded"), "age_s": age}


def reading_view(row: dict[str, Any] | None, *, now: datetime, stale_s: float, high: float, low: float,
                 critical_high: float | None = None) -> dict[str, Any]:
    if row is None:
        return {"value": None, "display": "UNKNOWN", "fresh": False, "level": "unknown", "age_s": None}
    age = age_seconds(row.get("gateway_ingest_ts"), now)
    fresh = is_fresh(age, stale_s)
    value = row.get("value")
    level = "normal"
    if value is not None:
        if critical_high is not None and value >= critical_high:
            level = "critical"
        elif value >= high:
            level = "warning"
    return {"value": value, "unit": row.get("unit"), "device_ts": row.get("device_ts"),
            "gateway_ingest_ts": row.get("gateway_ingest_ts"), "quality": row.get("quality"),
            "validation_result": row.get("validation_result"), "fresh": fresh,
            "display": "FRESH" if fresh else "STALE", "level": level if fresh else "stale", "age_s": age,
            "low_threshold": low, "high_threshold": high}


def group_display(members: list[dict[str, Any]]) -> str:
    displays = {m["display"] for m in members}
    if displays == {"ON"}:
        return "ON"
    if displays == {"OFF"}:
        return "OFF"
    if "STALE" in displays or "UNKNOWN" in displays:
        return "STALE"
    return "MIXED"
