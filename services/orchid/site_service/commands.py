"""Remote command gate: schema, freshness window, expiry, dedup (bounded LRU)."""
from __future__ import annotations

import json
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from orchid.core.config import CommandCfg, PayloadLimits
from orchid.core.jsonsafe import PayloadError, parse_json_bounded
from orchid.core.timeutil import parse_aware

ISSUED_SKEW_SECONDS = 5
_TEXT = r"^[A-Za-z0-9_.@:\- ]{1,64}$"


class _CommandBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str
    issued_at: str
    expires_at: str
    operator: str = Field(pattern=_TEXT)
    reason: str = Field(pattern=_TEXT)

    @field_validator("command_id")
    @classmethod
    def _uuid(cls, v: str) -> str:
        return str(uuid.UUID(v))

    @field_validator("operator", "reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _ts(cls, v: str) -> str:
        parse_aware(v)
        return v


class GroupCommand(_CommandBase):
    desired_state: Literal["ON", "OFF"]


class ModeCommand(_CommandBase):
    mode: Literal["AUTO", "MANUAL_OVERRIDE", "MAINTENANCE"]
    ttl_seconds: int | None = Field(default=None, gt=0, le=86400)


@dataclass(frozen=True)
class GateResult:
    ok: bool
    code: str
    command: GroupCommand | ModeCommand | None = None
    previous: dict[str, Any] | None = None
    detail: str = ""
    command_id: str | None = None  # best-effort id of an invalid command, so its ACK can be correlated


def _uuid_or_none(value: Any) -> str | None:
    try:
        return str(uuid.UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


class CommandGate:
    def __init__(self, cfg: CommandCfg, limits: PayloadLimits) -> None:
        self.cfg = cfg
        self.limits = limits
        self._seen: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.generation = 0  # increments on every remember(); drives persistence even when the LRU is full

    def __len__(self) -> int:
        return len(self._seen)

    def remember(self, command_id: str, ack: dict[str, Any]) -> None:
        self._seen[command_id] = ack
        self._seen.move_to_end(command_id)
        self.generation += 1
        while len(self._seen) > self.cfg.dedup_cache_size:
            self._seen.popitem(last=False)

    def parse_bytes(self, raw: bytes) -> dict[str, Any] | GateResult:
        try:
            obj = parse_json_bounded(raw, max_bytes=self.limits.max_command_bytes, max_depth=self.limits.max_json_depth)
        except PayloadError as exc:
            return GateResult(False, "INVALID_COMMAND", detail=exc.code)
        if not isinstance(obj, dict):
            return GateResult(False, "INVALID_COMMAND", detail="not an object")
        return obj

    def _check(self, model: type[GroupCommand] | type[ModeCommand], body: dict[str, Any], now: datetime) -> GateResult:
        try:
            cmd = model.model_validate(body)
        except ValidationError as exc:
            first = exc.errors()[0]
            return GateResult(False, "INVALID_COMMAND", detail=f"{first.get('loc')}: {first.get('type')}",
                              command_id=_uuid_or_none(body.get("command_id")))
        issued, expires = parse_aware(cmd.issued_at), parse_aware(cmd.expires_at)
        if expires <= issued:
            return GateResult(False, "INVALID_COMMAND", cmd, detail="expires_at <= issued_at")
        if (expires - issued).total_seconds() > self.cfg.max_ttl_seconds:
            return GateResult(False, "INVALID_COMMAND", cmd, detail="ttl exceeds max_ttl_seconds")
        if issued > now + timedelta(seconds=ISSUED_SKEW_SECONDS):
            return GateResult(False, "INVALID_COMMAND", cmd, detail="issued_at in the future")
        if cmd.command_id in self._seen:
            return GateResult(False, "DUPLICATE", cmd, previous=self._seen[cmd.command_id])
        if now > expires:
            return GateResult(False, "COMMAND_EXPIRED", cmd, detail="now > expires_at")
        return GateResult(True, "OK", cmd)

    def check_group_command(self, body: dict[str, Any], now: datetime) -> GateResult:
        return self._check(GroupCommand, body, now)

    def check_mode_command(self, body: dict[str, Any], now: datetime) -> GateResult:
        return self._check(ModeCommand, body, now)

    def export_ids(self) -> str:
        return json.dumps(list(self._seen.keys())[-self.cfg.dedup_cache_size:])
