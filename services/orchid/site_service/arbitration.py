"""Control authority state machine and output arbitration.

Priority (highest first):
    HARD_SAFETY_INTERLOCK > FAILSAFE > MAINTENANCE > MANUAL_OVERRIDE > AUTO_RULE

Operator-set base mode: AUTO | MANUAL_OVERRIDE | MAINTENANCE.
FAILSAFE is entered/exited automatically by data health; it overlays the base mode.
Every transition is recorded (drain_transitions) for local log + central audit.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from orchid.core.config import SiteConfig
from orchid.core.timeutil import iso


@dataclass(frozen=True)
class Override:
    operator: str
    reason: str
    created_at: datetime
    expires_at: datetime
    desired: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"operator": self.operator, "reason": self.reason, "created_at": iso(self.created_at),
                "expires_at": iso(self.expires_at), "desired": dict(self.desired)}


@dataclass(frozen=True)
class Maintenance:
    operator: str
    reason: str
    created_at: datetime
    expires_at: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {"operator": self.operator, "reason": self.reason, "created_at": iso(self.created_at),
                "expires_at": iso(self.expires_at) if self.expires_at else None}


def _require(operator: str, reason: str) -> None:
    if not operator or not operator.strip():
        raise ValueError("operator is required")
    if not reason or not reason.strip():
        raise ValueError("reason is required")


class ControlAuthority:
    def __init__(self, site: SiteConfig) -> None:
        self.site = site
        self.base_mode = "AUTO"
        self.override: Override | None = None
        self.maintenance: Maintenance | None = None
        self.failsafe = False
        self.failsafe_reason: str | None = None
        self._transitions: list[dict[str, Any]] = []

    # ---------------- queries ----------------
    def effective_mode(self) -> str:
        return "FAILSAFE" if self.failsafe else self.base_mode

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.effective_mode(),
            "base_mode": self.base_mode,
            "override": self.override.as_dict() if self.override else None,
            "maintenance": self.maintenance.as_dict() if self.maintenance else None,
            "failsafe_reason": self.failsafe_reason,
        }

    def arbitrate(self, group: str, auto_desired: str, interlock_active: bool) -> tuple[str, str]:
        if interlock_active:
            return "ON", "HARD_SAFETY_INTERLOCK"
        if self.failsafe:
            return self.site.failsafe_outputs[group], "FAILSAFE"
        if self.base_mode == "MAINTENANCE":
            return self.site.maintenance_outputs[group], "MAINTENANCE"
        if self.base_mode == "MANUAL_OVERRIDE" and self.override and group in self.override.desired:
            return self.override.desired[group], "MANUAL_OVERRIDE"
        return auto_desired, "AUTO_RULE"

    # ---------------- transitions ----------------
    def _record(self, before: str, cause: str, now: datetime, **extra: Any) -> dict[str, Any]:
        entry = {"from": before, "to": self.effective_mode(), "cause": cause, "at": iso(now), **extra}
        self._transitions.append(entry)
        return entry

    def drain_transitions(self) -> list[dict[str, Any]]:
        out, self._transitions = self._transitions, []
        return out

    def _ttl(self, ttl_s: float | None) -> float:
        if ttl_s is None or ttl_s <= 0:
            raise ValueError("override ttl must be > 0")
        return min(float(ttl_s), float(self.site.manual_override_ttl_seconds))

    def enter_manual_override(
        self, *, operator: str, reason: str, now: datetime, ttl_s: float | None, desired: dict[str, str]
    ) -> Override:
        _require(operator, reason)
        ttl = self._ttl(ttl_s)
        before = self.effective_mode()
        merged = dict(self.override.desired) if self.override and self.base_mode == "MANUAL_OVERRIDE" else {}
        merged.update(desired)
        self.override = Override(operator, reason, now, now + timedelta(seconds=ttl), merged)
        self.maintenance = None
        changed = self.base_mode != "MANUAL_OVERRIDE"
        self.base_mode = "MANUAL_OVERRIDE"
        if changed:
            self._record(before, "OPERATOR_REQUEST", now, operator=operator, reason=reason,
                         expires_at=iso(self.override.expires_at))
        return self.override

    def set_override_group(self, group: str, state: str) -> None:
        if self.override is None:
            raise ValueError("no active override")
        self.override = replace(self.override, desired={**self.override.desired, group: state})

    def enter_maintenance(self, *, operator: str, reason: str, now: datetime, ttl_s: float | None) -> Maintenance:
        _require(operator, reason)
        before = self.effective_mode()
        expires = now + timedelta(seconds=float(ttl_s)) if ttl_s else None
        self.maintenance = Maintenance(operator, reason, now, expires)
        self.override = None
        self.base_mode = "MAINTENANCE"
        self._record(before, "OPERATOR_REQUEST", now, operator=operator, reason=reason)
        return self.maintenance

    def return_to_auto(self, *, operator: str, reason: str, now: datetime) -> None:
        _require(operator, reason)
        before = self.effective_mode()
        self.base_mode, self.override, self.maintenance = "AUTO", None, None
        self._record(before, "OPERATOR_REQUEST", now, operator=operator, reason=reason)

    def set_failsafe(self, active: bool, *, reason: str, now: datetime) -> bool:
        if active == self.failsafe:
            return False
        before = self.effective_mode()
        self.failsafe = active
        self.failsafe_reason = reason if active else None
        self._record(before, reason, now)
        return True

    def tick(self, now: datetime) -> list[dict[str, Any]]:
        """Expire TTL-bound modes. Returns the transitions produced by this tick."""
        produced: list[dict[str, Any]] = []
        if self.base_mode == "MANUAL_OVERRIDE" and self.override and now >= self.override.expires_at:
            before = self.effective_mode()
            operator = self.override.operator
            self.base_mode, self.override = "AUTO", None
            produced.append(self._record(before, "OVERRIDE_TTL_EXPIRED", now, operator=operator))
        if (self.base_mode == "MAINTENANCE" and self.maintenance and self.maintenance.expires_at
                and now >= self.maintenance.expires_at):
            before = self.effective_mode()
            self.base_mode, self.maintenance = "AUTO", None
            produced.append(self._record(before, "MAINTENANCE_TTL_EXPIRED", now))
        return produced
