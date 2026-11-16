"""SiteEngine: local greenhouse control for one site, independent of central.

Received -> Validated -> Fresh -> Plausible -> Decision -> Group command -> ACK
-> Observed state -> Event/Audit. The engine performs no I/O: it consumes
(topic, bytes, now) and returns Outbound messages for the MQTT runner.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from orchid.core import topics
from orchid.core.config import GROUPS, Config
from orchid.core.logjson import SafeJsonLogger
from orchid.core.timeutil import iso
from orchid.core.validation import SensorValidator, Verdict
from orchid.site_service.actuators import GroupResult, RelayBank
from orchid.site_service.arbitration import ControlAuthority
from orchid.site_service.commands import CommandGate, GateResult
from orchid.site_service.rules import HysteresisNofM

GROUP_METRIC = {"fan_group": "temperature", "dehumidifier_group": "humidity"}
AUTO_REASON = {("fan_group", "ON"): "TEMP_HIGH", ("fan_group", "OFF"): "TEMP_NORMAL",
               ("dehumidifier_group", "ON"): "HUMIDITY_HIGH", ("dehumidifier_group", "OFF"): "HUMIDITY_NORMAL"}
QUALITY_EVENTS = {"REPLAY_OR_DUPLICATE": "REPLAY_DETECTED", "IDENTITY_MISMATCH": "IDENTITY_MISMATCH",
                  "SPIKE_PENDING": "PLAUSIBILITY_FAIL"}
EVENT_MIN_INTERVAL_S = 10.0
REJECT_REPORTS_PER_SECOND = 3
INTERLOCK_RELEASE_BAND = 1.0


@dataclass(frozen=True)
class Out:
    topic: str
    payload: dict[str, Any]
    qos: int = 1
    retain: bool = False
    expiry: int | None = None

    def encode(self) -> bytes:
        return json.dumps(self.payload, separators=(",", ":"), ensure_ascii=True).encode()


class SiteEngine:
    def __init__(self, cfg: Config, site_id: str, log: SafeJsonLogger | None = None) -> None:
        self.cfg = cfg
        self.site_id = site_id
        self.site = cfg.sites[site_id]
        self.timing = cfg.platform.timing
        self.source = f"{site_id}-controller"
        self.log = log or SafeJsonLogger(self.source)
        self.validators = {
            dev: SensorValidator(self.site, cfg.platform.payload_limits, dev, metric)
            for dev, metric in self.site.sensors.items()
        }
        n, m = self.site.confirmation.n, self.site.confirmation.m
        self.rules = {
            metric: HysteresisNofM(*self.site.thresholds(metric), n=n, m=m, max_age_s=self.site.stale_seconds)
            for metric in ("temperature", "humidity")
        }
        hi = self.site.interlock.temperature_high
        self.interlock = HysteresisNofM(hi, hi - INTERLOCK_RELEASE_BAND, n=n, m=m, max_age_s=self.site.stale_seconds)
        self.authority = ControlAuthority(self.site)
        self.gate = CommandGate(cfg.platform.command, cfg.platform.payload_limits)
        self.bank = RelayBank(self.site.actuator_groups)
        self.started_at: datetime | None = None
        self.last_eligible: dict[str, datetime | None] = {"temperature": None, "humidity": None}
        self.last_verdict: dict[str, Verdict | None] = {"temperature": None, "humidity": None}
        self.groups: dict[str, dict[str, Any]] = {}
        self.active_alerts: set[str] = set()
        self._last_event_at: dict[str, datetime] = {}
        self._reject_window: dict[str, tuple[int, int]] = {}
        self._last_heartbeat: datetime | None = None
        self._last_state_pub: datetime | None = None
        self._hb_seq = 0
        self.counters = {"telemetry_validated": 0, "telemetry_rejected": 0, "commands": 0}

    # ------------------------------------------------------------------ helpers
    def lwt(self) -> tuple[str, bytes]:
        body = {"status": "OFFLINE", "site_id": self.site_id, "reason": "LWT", "source": self.source}
        return topics.heartbeat(self.site_id), json.dumps(body).encode()

    def _event(self, event_type: str, now: datetime, alert: bool = False, **details: Any) -> Out:
        payload = {"event_id": str(uuid.uuid4()), "event_type": event_type, "ts": iso(now), "details": details}
        level = "CRITICAL" if alert else "INFO"
        self.log.log(level, event_type, site=self.site_id, **details)
        topic = topics.alerts(self.site_id) if alert else topics.events(self.site_id)
        return Out(topic, payload, qos=1, retain=False)

    def _throttled(self, key: str, now: datetime) -> bool:
        last = self._last_event_at.get(key)
        if last and (now - last).total_seconds() < EVENT_MIN_INTERVAL_S:
            return True
        self._last_event_at[key] = now
        return False

    def _raise(self, key: str, alert_type: str, now: datetime, **details: Any) -> list[Out]:
        if key in self.active_alerts:
            return []
        self.active_alerts.add(key)
        return [self._event(alert_type, now, alert=True, state="RAISED", **details)]

    def _clear(self, key: str, recovered_type: str, now: datetime, **details: Any) -> list[Out]:
        if key not in self.active_alerts:
            return []
        self.active_alerts.discard(key)
        return [self._event(recovered_type, now, alert=True, state="CLEARED", cleared=key.split(":")[0], **details)]

    def _state_out(self, member: str, group: str, now: datetime) -> Out:
        observed = self.bank.observed(member)
        commanded = self.bank.commanded(member)
        quality = "GOOD" if observed == commanded else "BAD"
        payload = {"device_id": member, "group": group, "state": observed, "commanded": commanded,
                   "observed_at": iso(now), "quality": quality, "source": self.source}
        return Out(topics.actuator_state(self.site_id, member), payload, qos=1, retain=True)

    def _all_states(self, now: datetime) -> list[Out]:
        self._last_state_pub = now
        return [self._state_out(m, g, now) for g in GROUPS for m in self.site.actuator_groups[g]]

    def issues(self) -> list[str]:
        found = {key.split(":")[0] for key in self.active_alerts}
        if self.authority.failsafe:
            found.add("FAILSAFE")
        if self.log.degraded:
            found.add("LOCAL_LOG_DEGRADED")
        return sorted(found)

    def _heartbeat(self, now: datetime) -> Out:
        self._last_heartbeat = now
        self._hb_seq += 1
        issues = self.issues()
        payload = {"status": "ONLINE", "site_id": self.site_id, "health": "DEGRADED" if issues else "OK",
                   "issues": issues, "mode": self.authority.effective_mode(), "ts": iso(now), "seq": self._hb_seq,
                   "started_at": iso(self.started_at) if self.started_at else None, "source": self.source,
                   "sensors": {m: self._sensor_status(m, now) for m in self.last_eligible}}
        return Out(topics.heartbeat(self.site_id), payload, qos=1, retain=True)

    def _sensor_status(self, metric: str, now: datetime) -> str:
        if f"SENSOR_FROZEN:{metric}" in self.active_alerts:
            return "FROZEN"
        last = self.last_eligible[metric]
        if last is None or (now - last).total_seconds() > self.site.stale_seconds:
            return "STALE"
        return "OK"

    # ------------------------------------------------------------------ lifecycle
    def startup(self, now: datetime) -> list[Out]:
        self.started_at = now
        for group in GROUPS:
            res = self.bank.apply_group(group, "OFF")
            self.groups[group] = {"desired": "OFF", "source": "STARTUP", "result": res.result,
                                  "members": res.members, "last_attempt": now, "command_id": None}
        return [self._heartbeat(now), *self._all_states(now),
                self._event("CONTROLLER_STARTED", now, mode=self.authority.effective_mode())]

    def republish(self, now: datetime) -> list[Out]:
        """Retained ONLINE heartbeat + observed states (after every MQTT (re)connect)."""
        return [self._heartbeat(now), *self._all_states(now)]

    def on_message(self, topic: str, raw: bytes, now: datetime) -> list[Out]:
        parsed = topics.parse(topic)
        if parsed is None or parsed.site != self.site_id:
            return []
        if parsed.kind == "telemetry":
            return self._on_telemetry(parsed.device or "", parsed.metric or "", raw, now)
        if parsed.kind == "command" and parsed.group == "mode":
            return self._on_mode(raw, now)
        if parsed.kind == "command" and parsed.group in GROUPS:
            return self._on_group_command(parsed.group, raw, now)
        return []

    # ------------------------------------------------------------------ telemetry
    def _allow_reject_report(self, device: str, now: datetime) -> bool:
        second = int(now.timestamp())
        window, count = self._reject_window.get(device, (second, 0))
        if window != second:
            window, count = second, 0
        self._reject_window[device] = (window, count + 1)
        return count < REJECT_REPORTS_PER_SECOND

    def _on_telemetry(self, device: str, metric: str, raw: bytes, now: datetime) -> list[Out]:
        validator = self.validators.get(device)
        if validator is None:
            if self._throttled(f"unknown:{device}", now):
                return []
            return [self._event("IDENTITY_MISMATCH", now, device=device, detail="unregistered device")]
        verdict = validator.process(self.site_id, device, metric, raw, now)
        outs: list[Out] = []
        real_metric = validator.metric
        self.last_verdict[real_metric] = verdict
        if verdict.control_eligible and verdict.sample is not None:
            self.counters["telemetry_validated"] += 1
            self.last_eligible[real_metric] = now
            self.rules[real_metric].add(verdict.sample.value, now)
            if real_metric == "temperature":
                self.interlock.add(verdict.sample.value, now)
            outs += self._clear(f"SENSOR_FROZEN:{real_metric}", "SENSOR_RECOVERED", now, metric=real_metric)
        else:
            self.counters["telemetry_rejected"] += 1
            outs += self._quality_events(device, real_metric, verdict, now)
        if verdict.control_eligible or self._allow_reject_report(device, now):
            outs.append(self._gateway_telemetry(device, real_metric, verdict, now))
        return outs

    def _quality_events(self, device: str, metric: str, verdict: Verdict, now: datetime) -> list[Out]:
        if verdict.result == "FROZEN":
            return self._raise(f"SENSOR_FROZEN:{metric}", "SENSOR_FROZEN", now, device=device, metric=metric)
        event_type = QUALITY_EVENTS.get(verdict.result, "TELEMETRY_REJECTED")
        if verdict.result == "STALE":
            event_type = "TELEMETRY_REJECTED"
        if self._throttled(f"{event_type}:{device}:{verdict.result}", now):
            return []
        return [self._event(event_type, now, device=device, metric=metric, result=verdict.result,
                            detail=verdict.detail[:120])]

    def _gateway_telemetry(self, device: str, metric: str, verdict: Verdict, now: datetime) -> Out:
        payload: dict[str, Any] = {"schema_version": "1.0", "site_id": self.site_id, "device_id": device,
                                   "metric": metric, "gateway_ingest_ts": iso(now), "quality": verdict.quality,
                                   "validation_result": verdict.result, "stage": verdict.stage,
                                   "control_eligible": verdict.control_eligible}
        if verdict.sample is not None:
            s = verdict.sample
            payload.update(device_ts=s.device_ts.isoformat(), seq=s.seq, value=s.value, unit=s.unit)
        return Out(topics.gateway_telemetry(self.site_id, device, metric), payload, qos=1, retain=False)

    # ------------------------------------------------------------------ actuation
    def _apply(self, group: str, desired: str, source: str, reason: str, command_id: str, now: datetime,
               operator: str | None = None) -> list[Out]:
        res = self.bank.apply_group(group, desired)
        gs = self.groups[group]
        gs.update(desired=desired, source=source, result=res.result, members=res.members,
                  last_attempt=now, command_id=command_id)
        ack = self._ack(group, res, command_id, source, reason, now, operator=operator)
        outs = [ack, *[self._state_out(m, group, now) for m in self.site.actuator_groups[group]]]
        outs.append(self._event("COMMAND_APPLIED", now, group=group, requested_state=desired, result=res.result,
                                source=source, reason=reason, command_id=command_id))
        return outs + self._result_alerts(group, res, now)

    def _ack(self, group: str, res: GroupResult, command_id: str, source: str, reason: str, now: datetime,
             operator: str | None = None) -> Out:
        payload = {"command_id": command_id, "site_id": self.site_id, "group": group,
                   "requested_state": res.requested_state, "result": res.result, "members": dict(res.members),
                   "source": source, "reason": reason, "mode": self.authority.effective_mode(),
                   "operator": operator, "acked_at": iso(now)}
        return Out(topics.group_ack(self.site_id, group), payload, qos=1, retain=False)

    def _result_alerts(self, group: str, res: GroupResult, now: datetime) -> list[Out]:
        members = ",".join(f"{k}={v}" for k, v in sorted(res.members.items()))
        outs: list[Out] = []
        partial, failed = f"ACTUATOR_PARTIAL_FAILURE:{group}", f"ACTUATOR_FAILURE:{group}"
        if res.result == "PARTIAL_FAILURE":
            outs += self._clear(failed, "ACTUATOR_RECOVERED", now, group=group, members=members)
            outs += self._raise(partial, "ACTUATOR_PARTIAL_FAILURE", now, group=group,
                                requested_state=res.requested_state, members=members)
        elif res.result == "FAILED":
            outs += self._clear(partial, "ACTUATOR_RECOVERED", now, group=group, members=members)
            outs += self._raise(failed, "ACTUATOR_FAILURE", now, group=group,
                                requested_state=res.requested_state, members=members)
        else:
            outs += self._clear(partial, "ACTUATOR_RECOVERED", now, group=group, members=members)
            outs += self._clear(failed, "ACTUATOR_RECOVERED", now, group=group, members=members)
        return outs

    def set_actuator_fault(self, member: str, fault: str | None) -> None:
        self.bank.set_fault(member, fault)

    # ------------------------------------------------------------------ commands
    def _reject_ack(self, topic_group: str, gate: GateResult, result: str, detail: str, now: datetime,
                    requested: str | None = None) -> Out:
        cmd = gate.command
        payload: dict[str, Any] = {
            "command_id": cmd.command_id if cmd else None, "site_id": self.site_id, "group": topic_group,
            "requested_state": requested, "result": result, "detail": detail, "source": "REMOTE",
            "mode": self.authority.effective_mode(), "acked_at": iso(now),
            "members": dict(self.groups[topic_group]["members"]) if topic_group in self.groups else {},
        }
        if gate.code == "DUPLICATE" and gate.previous:
            payload["original_result"] = gate.previous.get("result")
        return Out(topics.group_ack(self.site_id, topic_group), payload, qos=1, retain=False)

    def _gate_failure(self, topic_group: str, gate: GateResult, now: datetime, requested: str | None) -> list[Out]:
        cid = gate.command.command_id if gate.command else None
        if gate.code == "COMMAND_EXPIRED":
            return [self._reject_ack(topic_group, gate, "EXPIRED", "now > expires_at", now, requested),
                    self._event("COMMAND_EXPIRED", now, group=topic_group, command_id=cid)]
        if gate.code == "DUPLICATE":
            return [self._reject_ack(topic_group, gate, "DUPLICATE", "already processed", now, requested),
                    self._event("COMMAND_DUPLICATE", now, group=topic_group, command_id=cid)]
        return [self._reject_ack(topic_group, gate, "INVALID", gate.detail[:120], now, requested),
                self._event("COMMAND_INVALID", now, group=topic_group, detail=gate.detail[:120])]

    def _on_group_command(self, group: str, raw: bytes, now: datetime) -> list[Out]:
        self.counters["commands"] += 1
        parsed = self.gate.parse_bytes(raw)
        gate = parsed if isinstance(parsed, GateResult) else self.gate.check_group_command(parsed, now)
        requested = getattr(gate.command, "desired_state", None)
        if not gate.ok:
            return self._gate_failure(group, gate, now, requested)
        cmd = gate.command
        assert cmd is not None and requested is not None
        refusal = None
        if self.authority.failsafe:
            refusal = "FAILSAFE_ACTIVE"
        elif self.authority.base_mode == "MAINTENANCE":
            refusal = "MAINTENANCE_ACTIVE"
        elif group == "fan_group" and self.interlock.state and requested != "ON":
            refusal = "INTERLOCK_ACTIVE"
        if refusal:
            ack = self._reject_ack(group, gate, "REJECTED", refusal, now, requested)
            self.gate.remember(cmd.command_id, ack.payload)
            return [ack, self._event("COMMAND_REJECTED", now, group=group, command_id=cmd.command_id,
                                     detail=refusal, operator=cmd.operator)]
        outs: list[Out] = []
        if self.authority.base_mode != "MANUAL_OVERRIDE":
            self.authority.enter_manual_override(operator=cmd.operator, reason=cmd.reason, now=now,
                                                 ttl_s=self.site.manual_override_ttl_seconds,
                                                 desired={group: requested})
            outs += self._mode_events(now)
        else:
            self.authority.set_override_group(group, requested)
        desired, source = self.authority.arbitrate(group, self._auto(group), self._interlock_for(group))
        outs += self._apply(group, desired, "REMOTE" if source == "MANUAL_OVERRIDE" else source, cmd.reason,
                            cmd.command_id, now, operator=cmd.operator)
        self.gate.remember(cmd.command_id, {"result": self.groups[group]["result"]})
        return outs

    def _on_mode(self, raw: bytes, now: datetime) -> list[Out]:
        self.counters["commands"] += 1
        parsed = self.gate.parse_bytes(raw)
        gate = parsed if isinstance(parsed, GateResult) else self.gate.check_mode_command(parsed, now)
        requested = getattr(gate.command, "mode", None)
        if not gate.ok:
            return self._gate_failure_mode(gate, now, requested)
        cmd = gate.command
        assert cmd is not None
        if cmd.mode == "AUTO":
            self.authority.return_to_auto(operator=cmd.operator, reason=cmd.reason, now=now)
        elif cmd.mode == "MANUAL_OVERRIDE":
            self.authority.enter_manual_override(operator=cmd.operator, reason=cmd.reason, now=now,
                                                 ttl_s=cmd.ttl_seconds or self.site.manual_override_ttl_seconds,
                                                 desired={})
        else:
            self.authority.enter_maintenance(operator=cmd.operator, reason=cmd.reason, now=now,
                                             ttl_s=cmd.ttl_seconds)
        result = {"result": "APPLIED"}
        self.gate.remember(cmd.command_id, result)
        payload = {"command_id": cmd.command_id, "site_id": self.site_id, "group": "mode", "requested_mode": cmd.mode,
                   "result": "APPLIED", "mode": self.authority.effective_mode(), "operator": cmd.operator,
                   "detail": "FAILSAFE overlays base mode" if self.authority.failsafe else "", "acked_at": iso(now)}
        outs = [Out(topics.mode_ack(self.site_id), payload, qos=1, retain=False), *self._mode_events(now)]
        return outs + self._evaluate_groups(now)

    def _gate_failure_mode(self, gate: GateResult, now: datetime, requested: str | None) -> list[Out]:
        outs = self._gate_failure("mode", gate, now, None)
        ack = outs[0]
        payload = {k: v for k, v in ack.payload.items() if k not in ("members", "requested_state")}
        payload["requested_mode"] = requested
        return [Out(ack.topic, payload, qos=1, retain=False), *outs[1:]]

    def _mode_events(self, now: datetime) -> list[Out]:
        outs: list[Out] = []
        for tr in self.authority.drain_transitions():
            outs.append(self._event("MODE_CHANGED", now, **{"from": tr["from"], "to": tr["to"], "cause": tr["cause"],
                                                            "operator": tr.get("operator"),
                                                            "reason": tr.get("reason")}))
            if tr["cause"] == "OVERRIDE_TTL_EXPIRED":
                outs.append(self._event("OVERRIDE_EXPIRED", now, operator=tr.get("operator")))
            if tr["to"] == "FAILSAFE":
                outs.append(self._event("FAILSAFE_ENTERED", now, alert=True, cause=tr["cause"]))
            elif tr["from"] == "FAILSAFE":
                outs.append(self._event("FAILSAFE_EXITED", now, alert=True, cause=tr["cause"]))
        return outs

    # ------------------------------------------------------------------ periodic evaluation
    def _auto(self, group: str) -> str:
        return "ON" if self.rules[GROUP_METRIC[group]].state else "OFF"

    def _interlock_for(self, group: str) -> bool:
        return group == "fan_group" and self.interlock.state

    def _data_health(self, now: datetime) -> list[Out]:
        outs: list[Out] = []
        assert self.started_at is not None
        unhealthy = []
        for metric, last in self.last_eligible.items():
            since = last or self.started_at
            if (now - since).total_seconds() > self.site.failsafe_after_seconds:
                unhealthy.append(metric)
            if last is None or (now - last).total_seconds() > self.site.stale_seconds:
                outs += self._raise(f"SENSOR_STALE:{metric}", "SENSOR_STALE", now, metric=metric)
            else:
                outs += self._clear(f"SENSOR_STALE:{metric}", "SENSOR_RECOVERED", now, metric=metric)
        if unhealthy and not self.authority.failsafe:
            self.authority.set_failsafe(True, reason="SENSOR_DATA_UNAVAILABLE", now=now)
            for rule in self.rules.values():
                rule.reset()
        elif self.authority.failsafe and not unhealthy:
            n = self.site.confirmation.n
            if all(rule.fresh_count(now) >= n for rule in self.rules.values()):
                self.authority.set_failsafe(False, reason="DATA_RECOVERED", now=now)
        return outs

    def _evaluate_groups(self, now: datetime) -> list[Out]:
        outs: list[Out] = []
        for group in GROUPS:
            desired, source = self.authority.arbitrate(group, self._auto(group), self._interlock_for(group))
            gs = self.groups[group]
            current = self.bank.read_group(group, gs["desired"])
            reason = AUTO_REASON[(group, desired)] if source == "AUTO_RULE" else source
            if desired != gs["desired"]:
                outs += self._apply(group, desired, source, reason, str(uuid.uuid4()), now)
            elif current.result != "APPLIED" and (now - gs["last_attempt"]).total_seconds() >= \
                    self.cfg.platform.command.retry_backoff_seconds:
                outs += self._apply(group, desired, source, f"RETRY_{reason}", str(uuid.uuid4()), now)
            elif current.result != gs["result"]:
                gs.update(result=current.result, members=current.members)
                outs += [self._state_out(m, group, now) for m in self.site.actuator_groups[group]]
                outs += self._result_alerts(group, current, now)
        return outs

    def tick(self, now: datetime) -> list[Out]:
        outs: list[Out] = []
        self.authority.tick(now)
        for rule in self.rules.values():
            rule.decide(now)
        was_interlock = self.interlock.state
        self.interlock.decide(now)
        if self.interlock.state != was_interlock:
            kind = "INTERLOCK_ACTIVE" if self.interlock.state else "INTERLOCK_CLEARED"
            outs.append(self._event(kind, now, alert=self.interlock.state,
                                    threshold=self.site.interlock.temperature_high))
            if self.interlock.state:
                self.active_alerts.add("INTERLOCK_ACTIVE:fan_group")
            else:
                self.active_alerts.discard("INTERLOCK_ACTIVE:fan_group")
        outs += self._data_health(now)
        outs += self._mode_events(now)
        outs += self._evaluate_groups(now)
        if self._last_heartbeat is None or \
                (now - self._last_heartbeat).total_seconds() >= self.timing.heartbeat_interval_seconds:
            outs.append(self._heartbeat(now))
        if self._last_state_pub is None or \
                (now - self._last_state_pub).total_seconds() >= self.timing.state_republish_seconds:
            outs += self._all_states(now)
        return outs

    # ------------------------------------------------------------------ status
    def snapshot(self, now: datetime) -> dict[str, Any]:
        groups = {}
        for group in GROUPS:
            gs = self.groups.get(group, {})
            live = self.bank.read_group(group, gs.get("desired", "OFF"))
            groups[group] = {"desired": gs.get("desired"), "source": gs.get("source"), "result": live.result,
                             "members": live.members, "command_id": gs.get("command_id")}
        sensors = {}
        for metric, verdict in self.last_verdict.items():
            sensors[metric] = {
                "status": self._sensor_status(metric, now),
                "last_result": verdict.result if verdict else None,
                "last_value": verdict.sample.value if verdict and verdict.sample else None,
                "last_eligible_at": iso(self.last_eligible[metric]) if self.last_eligible[metric] else None,
            }
        return {"site_id": self.site_id, "now": iso(now), "mode": self.authority.snapshot(), "groups": groups,
                "interlock_active": self.interlock.state, "sensors": sensors, "issues": self.issues(),
                "health": "DEGRADED" if self.issues() else "OK", "faults": self.bank.faults(),
                "counters": dict(self.counters),
                "log": {"dropped": self.log.dropped, "degraded": self.log.degraded,
                        "critical_buffered": len(self.log.critical_buffer)}}

    def dedup_ids(self) -> list[str]:
        return json.loads(self.gate.export_ids())

    def load_dedup(self, ids: list[str]) -> None:
        for cid in ids[-self.cfg.platform.command.dedup_cache_size:]:
            self.gate.remember(str(cid), {"result": "PREVIOUSLY_PROCESSED"})
