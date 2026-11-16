"""Site controller logic: hysteresis, N-of-M, arbitration, commands, group actuation."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from orchid.site_service.actuators import RelayBank
from orchid.site_service.arbitration import ControlAuthority
from orchid.site_service.commands import CommandGate
from orchid.site_service.rules import HysteresisNofM


# ---------------- hysteresis + N-of-M ----------------
def make_rule(t0):
    return HysteresisNofM(high=20.5, low=19.5, n=3, m=5, max_age_s=10)


def add_all(rule, t0, values, start=0):
    for i, v in enumerate(values):
        rule.add(v, t0 + timedelta(seconds=start + i))
    return rule.decide(t0 + timedelta(seconds=start + len(values)))


def test_single_high_sample_does_not_turn_on(t0):
    rule = make_rule(t0)
    assert add_all(rule, t0, [20.0, 20.0, 25.0, 20.0, 20.0]) is False


def test_n_of_m_high_turns_on_and_hysteresis_holds(t0):
    rule = make_rule(t0)
    assert add_all(rule, t0, [21.0, 21.0, 21.0]) is True
    # In-band values (between low and high) keep it ON: no oscillation at the threshold.
    assert add_all(rule, t0, [20.2, 20.0, 19.8, 20.4, 20.1], start=3) is True
    assert add_all(rule, t0, [19.4, 19.3, 19.2], start=8) is False


def test_old_samples_age_out(t0):
    rule = make_rule(t0)
    for i in range(3):
        rule.add(21.0, t0 + timedelta(seconds=i))
    # 60 s later the high samples are too old to count.
    assert rule.decide(t0 + timedelta(seconds=60)) is False
    assert rule.fresh_count(t0 + timedelta(seconds=60)) == 0


# ---------------- arbitration ----------------
@pytest.fixture()
def auth(site_cfg):
    return ControlAuthority(site_cfg)


def test_default_mode_auto(auth, t0):
    assert auth.effective_mode() == "AUTO"
    assert auth.arbitrate("fan_group", auto_desired="ON", interlock_active=False) == ("ON", "AUTO_RULE")


def test_priority_order(auth, t0):
    auth.enter_manual_override(operator="op1", reason="test", now=t0, ttl_s=60, desired={"fan_group": "OFF"})
    assert auth.arbitrate("fan_group", auto_desired="ON", interlock_active=False) == ("OFF", "MANUAL_OVERRIDE")
    # Group without explicit override follows AUTO.
    assert auth.arbitrate("dehumidifier_group", "ON", False) == ("ON", "AUTO_RULE")
    auth.enter_maintenance(operator="op1", reason="filter swap", now=t0, ttl_s=None)
    assert auth.arbitrate("fan_group", "ON", False) == ("OFF", "MAINTENANCE")
    auth.set_failsafe(True, reason="SENSOR_STALE", now=t0)
    assert auth.effective_mode() == "FAILSAFE"
    assert auth.arbitrate("fan_group", "OFF", False) == ("ON", "FAILSAFE")
    assert auth.arbitrate("dehumidifier_group", "ON", False) == ("OFF", "FAILSAFE")
    # Hard interlock beats everything.
    assert auth.arbitrate("fan_group", "OFF", True) == ("ON", "HARD_SAFETY_INTERLOCK")


def test_override_requires_operator_reason_and_expiry(auth, t0):
    with pytest.raises(ValueError):
        auth.enter_manual_override(operator="", reason="x", now=t0, ttl_s=60, desired={})
    with pytest.raises(ValueError):
        auth.enter_manual_override(operator="op", reason="", now=t0, ttl_s=60, desired={})
    with pytest.raises(ValueError):
        auth.enter_manual_override(operator="op", reason="x", now=t0, ttl_s=0, desired={})
    ov = auth.enter_manual_override(operator="op", reason="x", now=t0, ttl_s=60, desired={})
    assert ov.expires_at == t0 + timedelta(seconds=60)
    assert ov.created_at == t0


def test_override_ttl_is_capped_by_config(auth, site_cfg, t0):
    ov = auth.enter_manual_override(operator="op", reason="x", now=t0, ttl_s=10**6, desired={})
    assert ov.expires_at == t0 + timedelta(seconds=site_cfg.manual_override_ttl_seconds)


def test_override_ttl_returns_to_auto_and_records_transition(auth, t0):
    auth.enter_manual_override(operator="op", reason="x", now=t0, ttl_s=30, desired={"fan_group": "ON"})
    assert auth.tick(t0 + timedelta(seconds=29)) == []
    transitions = auth.tick(t0 + timedelta(seconds=31))
    assert auth.effective_mode() == "AUTO"
    assert transitions and transitions[0]["to"] == "AUTO" and transitions[0]["cause"] == "OVERRIDE_TTL_EXPIRED"
    assert auth.arbitrate("fan_group", "OFF", False) == ("OFF", "AUTO_RULE")


def test_all_transitions_are_recorded(auth, t0):
    auth.enter_manual_override(operator="op", reason="x", now=t0, ttl_s=30, desired={})
    auth.return_to_auto(operator="op", reason="done", now=t0)
    auth.set_failsafe(True, reason="SENSOR_STALE", now=t0)
    auth.set_failsafe(False, reason="DATA_RECOVERED", now=t0)
    history = auth.drain_transitions()
    assert [h["to"] for h in history] == ["MANUAL_OVERRIDE", "AUTO", "FAILSAFE", "AUTO"]
    assert all({"from", "to", "cause", "at"} <= set(h) for h in history)


# ---------------- command gate: expiry / dedup / validation ----------------
def cmd(t0, *, issued=0, expires=30, state="ON", cid=None, **extra):
    body = {
        "command_id": cid or str(uuid.uuid4()),
        "issued_at": (t0 + timedelta(seconds=issued)).isoformat(),
        "expires_at": (t0 + timedelta(seconds=expires)).isoformat(),
        "desired_state": state,
        "reason": "OPERATOR_REQUEST",
        "operator": "op1",
    }
    body.update(extra)
    return body


@pytest.fixture()
def gate(cfg):
    return CommandGate(cfg.platform.command, cfg.platform.payload_limits)


def test_valid_command_accepted(gate, t0):
    res = gate.check_group_command(cmd(t0), now=t0 + timedelta(seconds=1))
    assert res.ok and res.code == "OK"


def test_expired_command_dropped(gate, t0):
    res = gate.check_group_command(cmd(t0, expires=5), now=t0 + timedelta(seconds=6))
    assert not res.ok and res.code == "COMMAND_EXPIRED"


def test_duplicate_command_is_idempotent(gate, t0):
    body = cmd(t0)
    first = gate.check_group_command(body, now=t0)
    assert first.ok
    gate.remember(body["command_id"], {"result": "APPLIED"})
    second = gate.check_group_command(body, now=t0)
    assert not second.ok and second.code == "DUPLICATE"
    assert second.previous == {"result": "APPLIED"}


@pytest.mark.parametrize(
    "mutation,code",
    [
        ({"desired_state": "MAYBE"}, "INVALID_COMMAND"),
        ({"command_id": "not-a-uuid"}, "INVALID_COMMAND"),
        ({"injected": "x"}, "INVALID_COMMAND"),
    ],
)
def test_invalid_commands(gate, t0, mutation, code):
    res = gate.check_group_command(cmd(t0, **mutation), now=t0)
    assert res.code == code


def test_ttl_beyond_max_rejected(gate, t0):
    res = gate.check_group_command(cmd(t0, expires=10_000), now=t0)
    assert res.code == "INVALID_COMMAND"


def test_issued_in_future_rejected(gate, t0):
    res = gate.check_group_command(cmd(t0, issued=120, expires=150), now=t0)
    assert res.code == "INVALID_COMMAND"


def test_dedup_cache_bounded(cfg, t0):
    gate = CommandGate(cfg.platform.command, cfg.platform.payload_limits)
    for i in range(cfg.platform.command.dedup_cache_size + 50):
        gate.remember(f"id-{i}", {"result": "APPLIED"})
    assert len(gate) == cfg.platform.command.dedup_cache_size


# ---------------- group actuation ----------------
@pytest.fixture()
def bank(site_cfg):
    return RelayBank(site_cfg.actuator_groups)


def test_group_applies_both_members(bank):
    res = bank.apply_group("fan_group", "ON")
    assert res.result == "APPLIED"
    assert res.members == {"fan01": "ON", "fan02": "ON"}


def test_partial_failure_detected(bank):
    bank.set_fault("fan02", "STUCK_OFF")
    res = bank.apply_group("fan_group", "ON")
    assert res.result == "PARTIAL_FAILURE"
    assert res.members == {"fan01": "ON", "fan02": "OFF"}


def test_total_failure(bank):
    bank.set_fault("dehumidifier01", "NO_FEEDBACK")
    bank.set_fault("dehumidifier02", "STUCK_OFF")
    res = bank.apply_group("dehumidifier_group", "ON")
    assert res.result == "FAILED"
    assert res.members == {"dehumidifier01": "UNKNOWN", "dehumidifier02": "OFF"}


def test_fault_clear(bank):
    bank.set_fault("fan01", "STUCK_ON")
    assert bank.apply_group("fan_group", "OFF").result == "PARTIAL_FAILURE"
    bank.set_fault("fan01", None)
    assert bank.apply_group("fan_group", "OFF").result == "APPLIED"


def test_unknown_fault_rejected(bank):
    with pytest.raises(ValueError):
        bank.set_fault("fan01", "EXPLODE")
