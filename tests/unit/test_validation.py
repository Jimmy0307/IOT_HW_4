"""Data trust pipeline: RAW -> NORMALIZED -> VALIDATED."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from orchid.core.validation import SensorValidator
from tests.conftest import telemetry_bytes

TOPIC = ("site01", "temp01", "temperature")


def ts(t, seconds):
    return (t + timedelta(seconds=seconds)).isoformat()


@pytest.fixture()
def v(cfg, site_cfg):
    return SensorValidator(site_cfg, cfg.platform.payload_limits, "temp01", "temperature")


def feed(v, t0, i, value, *, seq=None, now_offset=0.2, **kw):
    raw = telemetry_bytes(device_ts=ts(t0, i), seq=seq if seq is not None else i + 1, value=value, **kw)
    return v.process(*TOPIC, raw, t0 + timedelta(seconds=i + now_offset))


def test_valid_sample_is_validated_and_control_eligible(v, t0):
    verdict = feed(v, t0, 0, 21.4)
    assert verdict.result == "VALIDATED"
    assert verdict.stage == "VALIDATED"
    assert verdict.quality == "GOOD"
    assert verdict.control_eligible is True
    assert verdict.sample.value == 21.4
    assert verdict.sample.gateway_ingest_ts is not None


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"value": None}, "SCHEMA_INVALID"),
        ({"value": "20abc"}, "SCHEMA_INVALID"),
        ({"value": "21.4"}, "SCHEMA_INVALID"),
        ({"value": True}, "SCHEMA_INVALID"),
        ({"seq": -1}, "SCHEMA_INVALID"),
        ({"seq": "5"}, "SCHEMA_INVALID"),
        ({"schema_version": "9.9"}, "SCHEMA_INVALID"),
        ({"metric": "pressure"}, "SCHEMA_INVALID"),
        ({"device_ts": "yesterday"}, "SCHEMA_INVALID"),
        ({"device_ts": "2026-11-17T00:00:00"}, "SCHEMA_INVALID"),  # naive timestamp
        ({"extra": 1}, "SCHEMA_INVALID"),
        ({"unit": "F"}, "UNIT_INVALID"),
        ({"unit": "K"}, "UNIT_INVALID"),
        ({"site_id": "site02"}, "IDENTITY_MISMATCH"),
        ({"device_id": "humidity01"}, "IDENTITY_MISMATCH"),
    ],
)
def test_schema_type_unit_identity_rejections(v, t0, overrides, code):
    raw = telemetry_bytes(**{"device_ts": ts(t0, 0), **overrides})
    verdict = v.process(*TOPIC, raw, t0)
    assert verdict.result == code
    assert verdict.control_eligible is False
    assert verdict.stage == "REJECTED"


@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
def test_non_finite_rejected(v, t0, token):
    raw = telemetry_bytes(device_ts=ts(t0, 0)).replace(b"21.4", token)
    verdict = v.process(*TOPIC, raw, t0)
    assert verdict.result == "NON_FINITE"
    assert not verdict.control_eligible


def test_malformed_and_oversized_json(v, t0):
    assert v.process(*TOPIC, b'{"value": 1', t0).result == "MALFORMED_JSON"
    big = json.dumps({"pad": "x" * 5000}).encode()
    assert v.process(*TOPIC, big, t0).result == "OVERSIZE"
    deep = b'{"a":{"b":{"c":{"d":{"e":{}}}}}}'
    assert v.process(*TOPIC, deep, t0).result == "TOO_DEEP"


def test_topic_metric_mismatch_rejected(v, t0):
    raw = telemetry_bytes(device_ts=ts(t0, 0))
    verdict = v.process("site01", "temp01", "humidity", raw, t0)
    assert verdict.result == "IDENTITY_MISMATCH"


def test_capability_range_enforced(v, t0):
    assert feed(v, t0, 0, 51.0).result == "RANGE_FAIL"
    assert feed(v, t0, 1, -10.5).result == "RANGE_FAIL"
    assert feed(v, t0, 2, 200.0).result == "RANGE_FAIL"


def test_humidity_physical_domain(cfg, site_cfg, t0):
    hv = SensorValidator(site_cfg, cfg.platform.payload_limits, "humidity01", "humidity")
    raw = telemetry_bytes(
        device_id="humidity01", metric="humidity", unit="%RH", value=100.5, device_ts=ts(t0, 0)
    )
    assert hv.process("site01", "humidity01", "humidity", raw, t0).result == "RANGE_FAIL"
    raw = telemetry_bytes(
        device_id="humidity01", metric="humidity", unit="%RH", value=-0.1, seq=2, device_ts=ts(t0, 0)
    )
    assert hv.process("site01", "humidity01", "humidity", raw, t0).result == "RANGE_FAIL"


def test_stale_timestamp_not_control_eligible(v, t0):
    raw = telemetry_bytes(device_ts=ts(t0, 0), seq=1)
    verdict = v.process(*TOPIC, raw, t0 + timedelta(seconds=60))
    assert verdict.result == "STALE"
    assert verdict.quality == "STALE"
    assert verdict.control_eligible is False


def test_future_timestamp_rejected(v, t0):
    raw = telemetry_bytes(device_ts=ts(t0, 60))
    assert v.process(*TOPIC, raw, t0).result == "TIMESTAMP_FUTURE"


def test_duplicate_and_backward_sequence_rejected(v, t0):
    assert feed(v, t0, 0, 21.0, seq=10).result == "VALIDATED"
    assert feed(v, t0, 1, 21.0, seq=10).result == "REPLAY_OR_DUPLICATE"
    assert feed(v, t0, 2, 21.0, seq=9).result == "REPLAY_OR_DUPLICATE"
    assert feed(v, t0, 3, 21.0, seq=11).result == "VALIDATED"


def test_single_spike_is_not_accepted(v, t0):
    for i in range(3):
        assert feed(v, t0, i, 20.0 + i * 0.01).result == "VALIDATED"
    spike = feed(v, t0, 3, 35.0)
    assert spike.result == "SPIKE_PENDING"
    assert spike.quality == "SUSPECT"
    assert spike.control_eligible is False
    # Return to normal: plausible again, spike forgotten.
    assert feed(v, t0, 4, 20.05).result == "VALIDATED"


def test_confirmed_step_change_is_eventually_accepted(v, t0):
    feed(v, t0, 0, 20.0)
    results = [feed(v, t0, i, 30.0 + (i * 0.01)).result for i in range(1, 5)]
    assert results[0] == "SPIKE_PENDING"
    assert "VALIDATED" in results  # mutually consistent step confirmed
    assert results.index("VALIDATED") == 2  # 3rd consistent sample (spike_confirm_samples=3)


def test_gradual_ramp_within_rate_is_validated(v, t0):
    for i in range(10):
        assert feed(v, t0, i, 20.0 + i * 1.5).result == "VALIDATED"


def test_frozen_sensor_detected(v, t0):
    results = [feed(v, t0, i, 21.0).result for i in range(40)]
    assert results[0] == "VALIDATED"
    assert results[-1] == "FROZEN"
    frozen_at = results.index("FROZEN")
    assert frozen_at >= 30  # frozen_seconds=30


def test_noisy_signal_not_frozen(v, t0):
    results = [feed(v, t0, i, 21.0 + (0.01 if i % 2 else 0.0)).result for i in range(40)]
    assert set(results) == {"VALIDATED"}
