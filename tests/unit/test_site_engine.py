"""SiteEngine end-to-end logic without a broker (pure, clock-injected)."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest

from orchid.core import topics
from orchid.site_service.engine import SiteEngine

SITE = "site01"


class Harness:
    def __init__(self, cfg, t0):
        self.cfg = cfg
        self.now = t0
        self.engine = SiteEngine(cfg, SITE)
        self.out = list(self.engine.startup(self.now))
        self.seq = {"temp01": 0, "humidity01": 0}

    def advance(self, seconds=1.0):
        self.now += timedelta(seconds=seconds)
        self.out += self.engine.tick(self.now)

    def send(self, device, value, *, metric=None, unit=None, seq=None, ts=None):
        metric = metric or ("temperature" if device == "temp01" else "humidity")
        unit = unit or ("C" if metric == "temperature" else "%RH")
        self.seq[device] += 1
        body = {"schema_version": "1.0", "site_id": SITE, "device_id": device,
                "device_ts": (ts or self.now).isoformat(), "seq": seq if seq is not None else self.seq[device],
                "metric": metric, "value": value, "unit": unit}
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.out += self.engine.on_message(topics.telemetry(SITE, device, metric), raw, self.now)

    def send_raw(self, device, metric, raw: bytes):
        self.out += self.engine.on_message(topics.telemetry(SITE, device, metric), raw, self.now)

    def step(self, temp, hum, n=1):
        for _ in range(n):
            self.send("temp01", temp)
            self.send("humidity01", hum)
            self.advance(1.0)

    def command(self, group, desired, *, ttl=30, cid=None, issued_offset=0.0, **extra):
        cid = cid or str(uuid.uuid4())
        issued = self.now + timedelta(seconds=issued_offset)
        body = {"command_id": cid, "issued_at": issued.isoformat(),
                "expires_at": (issued + timedelta(seconds=ttl)).isoformat(), "desired_state": desired,
                "operator": "op1", "reason": "OPERATOR_REQUEST", **extra}
        self.out += self.engine.on_message(topics.group_command(SITE, group), json.dumps(body).encode(), self.now)
        return cid

    def mode(self, mode, *, ttl=None, cid=None):
        cid = cid or str(uuid.uuid4())
        body = {"command_id": cid, "issued_at": self.now.isoformat(),
                "expires_at": (self.now + timedelta(seconds=30)).isoformat(), "mode": mode,
                "operator": "op1", "reason": "TEST"}
        if ttl:
            body["ttl_seconds"] = ttl
        self.out += self.engine.on_message(topics.mode_command(SITE), json.dumps(body).encode(), self.now)
        return cid

    def take(self):
        out, self.out = self.out, []
        return out

    def outs(self, suffix):
        return [o for o in self.out if o.topic.endswith(suffix)]

    def acks(self, group):
        return [o.payload for o in self.out if o.topic == topics.group_ack(SITE, group)]

    def events(self, event_type=None):
        evs = [o.payload for o in self.out if o.topic in (topics.events(SITE), topics.alerts(SITE))]
        return [e for e in evs if event_type is None or e["event_type"] == event_type]

    def group_observed(self, group):
        return self.engine.snapshot(self.now)["groups"][group]["members"]


@pytest.fixture()
def h(cfg, t0):
    harness = Harness(cfg, t0)
    harness.step(20.0, 60.0, n=6)  # healthy baseline (in band)
    harness.take()
    return harness


def test_startup_publishes_online_heartbeat_and_retained_state(cfg, t0):
    harness = Harness(cfg, t0)
    hb = [o for o in harness.out if o.topic == topics.heartbeat(SITE)]
    assert hb and hb[0].payload["status"] == "ONLINE" and hb[0].retain and hb[0].qos == 1
    states = [o for o in harness.out if "/actuator/" in o.topic]
    assert len(states) == 4 and all(o.retain and o.qos == 1 for o in states)
    assert {"state", "observed_at", "quality", "source"} <= set(states[0].payload)
    assert harness.engine.lwt()[1] and json.loads(harness.engine.lwt()[1])["status"] == "OFFLINE"


def test_gateway_telemetry_enriched(h):
    h.send("temp01", 20.1)
    gw = [o.payload for o in h.out if "/gateway/telemetry/" in o.topic]
    assert gw and {"gateway_ingest_ts", "quality", "validation_result"} <= set(gw[0])
    assert gw[0]["validation_result"] == "VALIDATED"


def test_temperature_high_turns_on_fan_group(h):
    h.step(21.5, 60.0, n=4)
    acks = h.acks("fan_group")
    assert acks and acks[-1]["result"] == "APPLIED" and acks[-1]["requested_state"] == "ON"
    assert acks[-1]["members"] == {"fan01": "ON", "fan02": "ON"}
    assert acks[-1]["reason"] == "TEMP_HIGH"
    assert h.group_observed("dehumidifier_group") == {"dehumidifier01": "OFF", "dehumidifier02": "OFF"}


def test_humidity_high_turns_on_dehumidifier_group(h):
    h.step(20.0, 64.0, n=4)
    assert h.group_observed("dehumidifier_group") == {"dehumidifier01": "ON", "dehumidifier02": "ON"}
    assert h.group_observed("fan_group") == {"fan01": "OFF", "fan02": "OFF"}


def test_hysteresis_turns_off_only_below_low(h):
    h.step(21.5, 60.0, n=4)
    h.step(20.0, 60.0, n=5)  # in band: stays ON
    assert h.group_observed("fan_group")["fan01"] == "ON"
    h.step(19.3, 60.0, n=4)
    assert h.group_observed("fan_group")["fan01"] == "OFF"


def test_single_spike_does_not_actuate(h):
    h.send("temp01", 35.0)  # implausible jump
    h.advance(1)
    h.step(20.0, 60.0, n=3)
    assert h.group_observed("fan_group") == {"fan01": "OFF", "fan02": "OFF"}
    assert h.events("PLAUSIBILITY_FAIL")


@pytest.mark.parametrize("raw", [b'{"value":', b'{"value": NaN}', b"null", b'{"schema_version":"1.0"}'])
def test_invalid_samples_never_control(h, raw):
    for _ in range(6):
        h.send_raw("temp01", "temperature", raw)
        h.send("humidity01", 60.0)
        h.advance(1)
    assert h.group_observed("fan_group")["fan01"] == "OFF"
    assert h.events("TELEMETRY_REJECTED")


def test_stale_samples_never_control(h):
    for _ in range(6):
        h.send("temp01", 25.0, ts=h.now - timedelta(seconds=120))
        h.send("humidity01", 60.0)
        h.advance(1)
    assert h.group_observed("fan_group")["fan01"] == "OFF"


def test_partial_group_failure_detected_and_site_degraded(h):
    h.engine.set_actuator_fault("fan02", "STUCK_OFF")
    h.step(21.5, 60.0, n=4)
    ack = h.acks("fan_group")[-1]
    assert ack["result"] == "PARTIAL_FAILURE"
    assert ack["members"] == {"fan01": "ON", "fan02": "OFF"}
    assert h.events("ACTUATOR_PARTIAL_FAILURE")
    h.advance(6)
    hb = [o.payload for o in h.out if o.topic == topics.heartbeat(SITE)][-1]
    assert hb["health"] == "DEGRADED"
    assert "ACTUATOR_PARTIAL_FAILURE" in hb["issues"]


def test_remote_command_enters_override_and_acks(h):
    cid = h.command("fan_group", "ON")
    ack = h.acks("fan_group")[-1]
    assert ack["command_id"] == cid and ack["result"] == "APPLIED" and ack["source"] == "REMOTE"
    snap = h.engine.snapshot(h.now)
    assert snap["mode"]["mode"] == "MANUAL_OVERRIDE"
    assert snap["mode"]["override"]["operator"] == "op1"


def test_expired_command_never_executes(h):
    cid = h.command("fan_group", "ON", ttl=5, issued_offset=-10)
    ack = h.acks("fan_group")[-1]
    assert ack["command_id"] == cid and ack["result"] == "EXPIRED"
    assert h.group_observed("fan_group")["fan01"] == "OFF"
    assert h.events("COMMAND_EXPIRED")
    assert h.engine.snapshot(h.now)["mode"]["mode"] == "AUTO"


def test_duplicate_command_idempotent(h):
    cid = h.command("dehumidifier_group", "ON")
    h.take()
    h.command("dehumidifier_group", "ON", cid=cid)
    ack = h.acks("dehumidifier_group")[-1]
    assert ack["result"] == "DUPLICATE" and ack["original_result"] == "APPLIED"
    assert not [o for o in h.out if "/actuator/" in o.topic]  # nothing re-applied


def test_override_beats_auto_until_ttl_then_auto(h, site_cfg):
    h.mode("MANUAL_OVERRIDE", ttl=20)
    h.command("fan_group", "OFF")
    h.step(21.5, 60.0, n=5)  # AUTO would turn fans ON
    assert h.group_observed("fan_group")["fan01"] == "OFF"
    h.step(21.5, 60.0, n=17)  # past TTL
    assert h.engine.snapshot(h.now)["mode"]["mode"] == "AUTO"
    assert h.group_observed("fan_group")["fan01"] == "ON"
    assert any(e["details"].get("cause") == "OVERRIDE_TTL_EXPIRED" for e in h.events("MODE_CHANGED"))


def test_maintenance_rejects_remote_group_command(h):
    h.mode("MAINTENANCE")
    h.command("fan_group", "ON")
    ack = h.acks("fan_group")[-1]
    assert ack["result"] == "REJECTED" and ack["detail"] == "MAINTENANCE_ACTIVE"


def test_interlock_blocks_remote_off_and_forces_fans_on(h):
    temp = 20.0
    while temp < 33.0:  # ramp within plausibility limits
        temp += 1.5
        h.step(temp, 60.0)
    h.step(33.0, 60.0, n=4)
    snap = h.engine.snapshot(h.now)
    assert snap["interlock_active"] is True
    h.command("fan_group", "OFF")
    ack = h.acks("fan_group")[-1]
    assert ack["result"] == "REJECTED" and ack["detail"] == "INTERLOCK_ACTIVE"
    assert h.group_observed("fan_group")["fan01"] == "ON"


def test_sensor_offline_enters_failsafe(h, site_cfg):
    for _ in range(int(site_cfg.failsafe_after_seconds) + 3):
        h.send("humidity01", 60.0)
        h.advance(1)
    snap = h.engine.snapshot(h.now)
    assert snap["mode"]["mode"] == "FAILSAFE"
    assert h.group_observed("fan_group")["fan01"] == site_cfg.failsafe_outputs["fan_group"]
    assert h.events("FAILSAFE_ENTERED")
    h.step(20.0, 60.0, n=5)
    assert h.engine.snapshot(h.now)["mode"]["mode"] == "AUTO"


def test_log_failure_does_not_stop_control(h):
    h.engine.log.fail_injected = True
    h.step(21.5, 60.0, n=4)
    assert h.group_observed("fan_group")["fan01"] == "ON"
    assert h.engine.log.dropped > 0
    h.advance(6)
    hb = [o.payload for o in h.out if o.topic == topics.heartbeat(SITE)][-1]
    assert "LOCAL_LOG_DEGRADED" in hb["issues"]


def test_command_topic_qos_and_retain_contract(h):
    h.step(21.5, 64.0, n=4)
    for o in h.out:
        if "/actuator/" in o.topic or o.topic.endswith("/heartbeat"):
            assert o.qos == 1 and o.retain
        elif o.topic.endswith("/ack") or o.topic.endswith("/alerts") or o.topic.endswith("/events"):
            assert o.qos == 1 and not o.retain
        elif "/gateway/telemetry/" in o.topic:
            assert not o.retain


@pytest.mark.parametrize("field", ["reason", "operator"])
def test_blank_operator_or_reason_is_invalid_not_a_crash(h, field):
    cid = h.command("fan_group", "ON", **{field: "   "})
    ack = h.acks("fan_group")[-1]
    assert ack["result"] == "INVALID"
    assert h.group_observed("fan_group")["fan01"] == "OFF"
    assert h.engine.snapshot(h.now)["mode"]["mode"] == "AUTO"


def test_blank_reason_in_mode_command_is_invalid(h):
    body = {"command_id": str(uuid.uuid4()), "issued_at": h.now.isoformat(),
            "expires_at": (h.now + timedelta(seconds=30)).isoformat(), "mode": "MAINTENANCE",
            "operator": "op1", "reason": " "}
    h.out += h.engine.on_message(topics.mode_command(SITE), json.dumps(body).encode(), h.now)
    assert h.engine.snapshot(h.now)["mode"]["mode"] == "AUTO"
    assert [o.payload["result"] for o in h.out if o.topic == topics.mode_ack(SITE)] == ["INVALID"]


def test_maintenance_without_ttl_gets_bounded_expiry(h, site_cfg):
    h.mode("MAINTENANCE")
    m = h.engine.snapshot(h.now)["mode"]["maintenance"]
    assert m["expires_at"] is not None


def test_retry_under_persistent_fault_does_not_spam_acks(h):
    h.engine.set_actuator_fault("fan02", "STUCK_OFF")
    h.step(21.5, 60.0, n=4)
    first = len(h.acks("fan_group"))
    h.step(21.5, 60.0, n=35)  # several retry periods
    assert len(h.acks("fan_group")) == first  # retries re-drive outputs but do not emit new ACKs
    assert h.engine.counters.get("actuation_retries", 0) >= 2


def test_dedup_generation_changes_even_when_cache_full(cfg):
    from orchid.site_service.commands import CommandGate
    gate = CommandGate(cfg.platform.command, cfg.platform.payload_limits)
    for i in range(cfg.platform.command.dedup_cache_size):
        gate.remember(f"id-{i}", {})
    before = gate.generation
    gate.remember("one-more", {})
    assert gate.generation == before + 1 and len(gate) == cfg.platform.command.dedup_cache_size
