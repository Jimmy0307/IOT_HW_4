"""Config loading and topic contract."""
from __future__ import annotations

import pytest

from orchid.core import topics
from orchid.core.config import ConfigError, load_config_dict


def test_six_sites_loaded_with_teacher_targets(cfg):
    assert sorted(cfg.sites) == [f"site0{i}" for i in range(1, 7)]
    for site in cfg.sites.values():
        assert site.target_temperature == 20.0
        assert site.target_humidity == 60.0


def test_mqtt_version_is_5(cfg):
    assert cfg.platform.mqtt_version == 5


def test_temperature_range_flagged_as_lab_assumption(cfg):
    contract = cfg.sites["site01"].sensor_contract["temperature"]
    assert contract.lab_assumption is True
    hum = cfg.sites["site01"].sensor_contract["humidity"]
    assert (hum.min, hum.max) == (0.0, 100.0)


def test_hysteresis_thresholds_are_separate_from_capability_range(site_cfg):
    high, low = site_cfg.thresholds("temperature")
    assert (high, low) == (20.5, 19.5)
    assert site_cfg.thresholds("humidity") == (62.0, 58.0)
    # Operational threshold must not be the sensor validity range.
    assert site_cfg.sensor_contract["temperature"].max != high


def test_invalid_confirmation_rejected(cfg):
    raw = cfg.raw_dict()
    raw["site_defaults"]["confirmation"] = {"n": 6, "m": 5}
    with pytest.raises(ConfigError):
        load_config_dict(raw)


def test_infinite_session_expiry_rejected(cfg):
    raw = cfg.raw_dict()
    raw["platform"]["sessions"]["controller_session_expiry_seconds"] = 4294967295
    with pytest.raises(ConfigError):
        load_config_dict(raw)


def test_topic_builders():
    assert topics.telemetry("site01", "temp01", "temperature") == (
        "orchid/v1/site01/sensor/temp01/telemetry/temperature"
    )
    assert topics.group_command("site01", "fan_group") == "orchid/v1/site01/control/fan_group/command"
    assert topics.group_ack("site01", "dehumidifier_group") == (
        "orchid/v1/site01/control/dehumidifier_group/ack"
    )
    assert topics.actuator_state("site03", "fan02") == "orchid/v1/site03/actuator/fan02/state"
    assert topics.heartbeat("site06") == "orchid/v1/site06/gateway/heartbeat"


@pytest.mark.parametrize(
    "topic,kind,site",
    [
        ("orchid/v1/site01/sensor/temp01/telemetry/temperature", "telemetry", "site01"),
        ("orchid/v1/site02/gateway/telemetry/temp01/temperature", "gateway_telemetry", "site02"),
        ("orchid/v1/site03/control/fan_group/command", "command", "site03"),
        ("orchid/v1/site04/control/mode/ack", "ack", "site04"),
        ("orchid/v1/site05/alerts", "alerts", "site05"),
        ("orchid/v1/site06/events", "events", "site06"),
        ("orchid/v1/site01/gateway/heartbeat", "heartbeat", "site01"),
        ("orchid/v1/site01/actuator/fan01/state", "state", "site01"),
    ],
)
def test_topic_parse(topic, kind, site):
    parsed = topics.parse(topic)
    assert parsed is not None
    assert parsed.kind == kind
    assert parsed.site == site


@pytest.mark.parametrize(
    "topic",
    [
        "orchid/v1/site01/sensor/temp01/telemetry",
        "orchid/v2/site01/alerts",
        "orchid/v1/../alerts",
        "orchid/v1/SITE01/alerts",
        "orchid/v1/site01/sensor/temp 01/telemetry/temperature",
        "orchid/v1/site01/alerts/extra",
    ],
)
def test_topic_parse_rejects_unknown(topic):
    assert topics.parse(topic) is None
