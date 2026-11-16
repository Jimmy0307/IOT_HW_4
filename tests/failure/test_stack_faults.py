"""Failure / fault-injection gates: invalid, stale, spike, partial failure, expired command,
central outage, store-and-forward, logging outage."""
from __future__ import annotations

import json
import time

import pytest

from tests import lab

pytestmark = [pytest.mark.stack, pytest.mark.slow]
OUTAGE: dict[str, str] = {}

INVALID_FAULTS = {
    "invalid_unit": {"UNIT_INVALID"},
    "string_value": {"SCHEMA_INVALID"},
    "null_value": {"SCHEMA_INVALID"},
    "nan_value": {"NON_FINITE"},
    "infinity_value": {"NON_FINITE"},
    "malformed_json": {"MALFORMED_JSON"},
    "identity_spoof": {"IDENTITY_MISMATCH"},
    "duplicate_seq": {"REPLAY_OR_DUPLICATE"},
    "backward_seq": {"REPLAY_OR_DUPLICATE"},
    "oversize": {"OVERSIZE"},
    "deep_json": {"TOO_DEEP"},
    "naive_ts": {"SCHEMA_INVALID"},
}


@pytest.fixture()
def site_reset():
    used: list[str] = []
    yield used
    for site in used:
        try:
            lab.reset_site(site)
        except AssertionError:
            pass


def fans_off(site: str) -> bool:
    return lab.members(site, "fan_group") == {"fan01": "OFF", "fan02": "OFF"}


@pytest.mark.gate("G11", "invalid sensor sample cannot control")
def test_g11_invalid_samples_never_drive_control(evidence, site_reset):
    site = "site05"
    site_reset.append(site)
    lab.baseline(site)
    observed = {}
    for fault, expected in INVALID_FAULTS.items():
        lab.sim(site, "POST", "/fault", {"device": "temp01", "fault": fault})
        lab.set_env(site, temp=30.0, jump=True)  # the invalid samples carry a value that WOULD trigger fans
        time.sleep(4.5)
        st = lab.status(site)
        observed[fault] = {"last_result": st["sensors"]["temperature"]["last_result"],
                           "fan_group": st["groups"]["fan_group"]["members"], "mode": st["mode"]["mode"]}
        lab.set_env(site, temp=19.0, jump=True)
        time.sleep(1.2)
        lab.sim(site, "POST", "/fault", {"device": "temp01", "fault": None})
        time.sleep(3.0)
        assert observed[fault]["last_result"] in expected, (fault, observed[fault])
        assert observed[fault]["fan_group"] == {"fan01": "OFF", "fan02": "OFF"}, (fault, observed[fault])
        assert observed[fault]["mode"] == "AUTO"
    evidence.add("per-fault validation result while value=30 C (would trigger fans if trusted)",
                 "sim fault injection + faultctl GET /status", 0, observed)
    central = lab.db("SELECT validation_result, COUNT(*) AS n FROM validated_telemetry WHERE site=? AND "
                     "metric='temperature' AND validation_result != 'VALIDATED' GROUP BY validation_result", (site,),
                     record="rejections recorded centrally (gateway telemetry)")
    assert {r["validation_result"] for r in central} >= {"UNIT_INVALID", "SCHEMA_INVALID", "NON_FINITE"}
    lab.db("SELECT event_type, COUNT(*) AS n FROM events WHERE site=? AND event_type IN "
           "('TELEMETRY_REJECTED','REPLAY_DETECTED','IDENTITY_MISMATCH') GROUP BY event_type", (site,),
           record="data-quality security events")


@pytest.mark.gate("G12", "stale sample cannot control")
def test_g12_stale_and_future_samples_never_drive_control(evidence, site_reset):
    site = "site05"
    site_reset.append(site)
    lab.baseline(site)
    for fault, expected in (("stale_ts", "STALE"), ("future_ts", "TIMESTAMP_FUTURE")):
        lab.sim(site, "POST", "/fault", {"device": "temp01", "fault": fault})
        lab.set_env(site, temp=30.0, jump=True)
        time.sleep(8)
        st = lab.status(site)
        evidence.add(f"{fault}: value 30 C but timestamp untrustworthy", "faultctl GET /status", 0,
                     {"temperature": st["sensors"]["temperature"], "fan_group": st["groups"]["fan_group"]})
        assert st["sensors"]["temperature"]["last_result"] == expected
        assert st["groups"]["fan_group"]["members"] == {"fan01": "OFF", "fan02": "OFF"}
        lab.set_env(site, temp=19.0, jump=True)
        time.sleep(1.2)
        lab.sim(site, "POST", "/fault", {"device": "temp01", "fault": None})
        time.sleep(4)


@pytest.mark.gate("G13", "spike does not immediately actuate")
def test_g13_single_spike_does_not_actuate(evidence, site_reset):
    site = "site01"
    site_reset.append(site)
    lab.baseline(site)
    t0 = lab.iso(lab.now_utc())
    lab.sim(site, "POST", "/fault", {"device": "temp01", "fault": "spike"}, record="one-shot +15 C spike")
    # compromised device: physically impossible 200 C sample
    lab.probe("pub", "--host", "site01-broker", "--user", "probe-site01-temp01",
              "--topic", "orchid/v1/site01/sensor/temp01/telemetry/temperature", "--payload",
              json.dumps({"schema_version": "1.0", "site_id": "site01", "device_id": "temp01",
                          "device_ts": lab.iso(lab.now_utc()), "seq": 1, "metric": "temperature", "value": 200.0,
                          "unit": "C"}), record="injected 200 C sample")
    time.sleep(8)
    st = lab.status(site)
    evidence.add("fan group after spike", "faultctl GET /status", 0, st["groups"]["fan_group"])
    assert fans_off(site)
    rows = lab.wait_until(lambda: (r := lab.db(
        "SELECT validation_result, value FROM validated_telemetry WHERE site=? AND metric='temperature' AND "
        "validation_result IN ('SPIKE_PENDING','RANGE_FAIL') AND central_ingest_ts >= ?", (site, t0)))
        and {x["validation_result"] for x in r} == {"SPIKE_PENDING", "RANGE_FAIL"} and r, 30, desc="spike rows")
    evidence.add("spike + impossible value classified, not VALIDATED", "SQL validated_telemetry", 0, rows)
    lab.db("SELECT event_type, details FROM events WHERE site=? AND event_type='PLAUSIBILITY_FAIL' AND ingest_ts >= ?",
           (site, t0), record="PLAUSIBILITY_FAIL event")


@pytest.mark.gate("G16", "group partial failure detected")
def test_g16_partial_group_failure(evidence, site_reset):
    site = "site06"
    site_reset.append(site)
    lab.baseline(site)
    t0 = lab.iso(lab.now_utc())
    lab.ctl(site, "POST", "/fault/actuator", {"member": "fan02", "fault": "STUCK_OFF"},
            record="inject fan02 STUCK_OFF (relay feedback)")
    lab.set_env(site, temp=23.0)
    ack = lab.wait_until(lambda: lab.db(
        "SELECT command_id, status, members, source FROM commands WHERE site=? AND grp='fan_group' AND "
        "status='PARTIAL_FAILURE' AND acked_at >= ?", (site, t0)), 40, desc="PARTIAL_FAILURE ack")
    evidence.add("group ACK aggregates member states", "SQL commands", 0, ack)
    assert json.loads(ack[0]["members"]) == {"fan01": "ON", "fan02": "OFF"}
    alert = lab.wait_until(lambda: lab.db(
        "SELECT event_type, severity, kind, principal, details FROM events WHERE site=? AND "
        "event_type='ACTUATOR_PARTIAL_FAILURE' AND ingest_ts >= ?", (site, t0)), 30, desc="alert")
    evidence.add("ACTUATOR_PARTIAL_FAILURE alert (server severity)", "SQL events", 0, alert)
    assert alert[0]["severity"] == "CRITICAL" and alert[0]["kind"] == "alert"
    hb = lab.wait_until(lambda: (r := lab.db("SELECT heartbeat FROM site_status WHERE site=?", (site,)))
                        and json.loads(r[0]["heartbeat"])["health"] == "DEGRADED" and r, 30, desc="DEGRADED")
    hbj = json.loads(hb[0]["heartbeat"])
    evidence.add("site health DEGRADED with issue", "SQL site_status", 0,
                 {"health": hbj["health"], "issues": hbj["issues"]})
    assert "ACTUATOR_PARTIAL_FAILURE" in hbj["issues"]
    lab.ctl(site, "POST", "/fault/clear", {})
    lab.wait_until(lambda: lab.db("SELECT 1 FROM events WHERE site=? AND event_type='ACTUATOR_RECOVERED' AND "
                                  "ingest_ts >= ?", (site, t0)), 40, desc="recovery after fault cleared")
    lab.set_env(site, temp=19.0)


def _expired_variant(evidence, site: str, *, mqtt_expiry: int, label: str) -> dict:
    lab.baseline(site)
    lab.run(["docker", "stop", "-t", "5", f"orchid-{site}-controller"], record=f"[{label}] stop command consumer")
    body = lab.command_body("ON", ttl=5)
    lab.send_command(site, "fan_group", body, expiry=mqtt_expiry,
                     record=f"[{label}] publish QoS1 fan_group ON: expires_at=+5s, MQTT Message Expiry={mqtt_expiry}s")
    time.sleep(12)
    lab.run(["docker", "start", f"orchid-{site}-controller"], record=f"[{label}] reconnect consumer after expiry")
    lab.wait_until(lambda: lab.status(site).get("mqtt_connected"), 60, desc="controller back")
    time.sleep(8)
    st = lab.status(site)
    row = lab.db("SELECT status, detail FROM commands WHERE command_id=?", (body["command_id"],),
                 record=f"[{label}] central record of the command")
    events = lab.db("SELECT event_type FROM events WHERE site=? AND details LIKE ?",
                    (site, f"%{body['command_id']}%"), record=f"[{label}] site events for this command")
    evidence.add(f"[{label}] fan group after reconnect", "faultctl GET /status", 0, st["groups"]["fan_group"])
    assert st["groups"]["fan_group"]["members"] == {"fan01": "OFF", "fan02": "OFF"}
    assert not row or row[0]["status"] != "APPLIED", row
    assert "COMMAND_APPLIED" not in {e["event_type"] for e in events}
    return {"row": row, "events": [e["event_type"] for e in events]}


@pytest.mark.gate("G18", "expired queued command never executes")
def test_g18_expired_queued_command_never_executes(evidence, site_reset):
    site = "site05"
    site_reset.append(site)
    broker_layer = _expired_variant(evidence, site, mqtt_expiry=5, label="A broker Message Expiry")
    evidence.note(f"variant A outcome: {broker_layer}")
    app_layer = _expired_variant(evidence, site, mqtt_expiry=120, label="B application expires_at")
    evidence.note(f"variant B outcome: {app_layer}")
    assert app_layer["row"] and app_layer["row"][0]["status"] == "EXPIRED", app_layer
    assert "COMMAND_EXPIRED" in app_layer["events"]


@pytest.mark.gate("G22", "central outage local control survives")
def test_g22_central_outage_local_control_survives(evidence, site_reset):
    site_reset.extend(["site02", "site03"])
    lab.baseline("site02")
    lab.baseline("site03")
    OUTAGE["start"] = lab.iso(lab.now_utc())
    lab.run(["docker", "stop", "-t", "3", "orchid-central-broker"], record="CENTRAL BROKER DOWN")
    lab.run(["docker", "network", "disconnect", "orchid_bridge03_net", "orchid-site03-broker"],
            record="WAN/bridge cut for site03")
    try:
        lab.set_env("site02", temp=23.0)
        lab.set_env("site03", hum=68.0)
        on2 = lab.wait_group("site02", "fan_group", "ON", 40)
        on3 = lab.wait_group("site03", "dehumidifier_group", "ON", 40)
        evidence.add("local AUTO control during central outage", "faultctl GET /status", 0,
                     {"site02 fan_group": on2, "site03 dehumidifier_group": on3,
                      "site02 mode": lab.status("site02")["mode"]["mode"]})
        time.sleep(12)  # accumulate data for store-and-forward
    finally:
        OUTAGE["end"] = lab.iso(lab.now_utc())
        lab.run(["docker", "start", "orchid-central-broker"], record="central broker restored")
        lab.run(["docker", "network", "connect", "--alias", "site03-broker", "orchid_bridge03_net",
                 "orchid-site03-broker"], record="site03 WAN restored")
    lab.set_env("site02", temp=19.0)
    lab.set_env("site03", hum=56.0)


@pytest.mark.gate("G23", "recovery store forward works")
def test_g23_recovery_and_store_and_forward(evidence):
    assert OUTAGE, "requires G22 outage window"
    lab.wait_until(lambda: lab.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", "orchid-central-broker"]
                                   ).stdout.strip() == "healthy", 60, desc="central healthy")

    def recovered():
        rows = lab.db("SELECT site, COUNT(*) AS n, SUM(late) AS late FROM validated_telemetry WHERE site IN "
                      "('site02','site03') AND gateway_ingest_ts BETWEEN ? AND ? GROUP BY site",
                      (OUTAGE["start"], OUTAGE["end"]))
        return rows if len(rows) == 2 and all(r["late"] > 0 for r in rows) else None

    rows = lab.wait_until(recovered, 90, interval=3, desc="queued outage data delivered")
    evidence.add("telemetry produced DURING outage, delivered after recovery (late=1)", "SQL validated_telemetry",
                 0, {"window": OUTAGE, "rows": rows})
    status = lab.wait_until(lambda: (r := lab.db("SELECT site, bridge_state FROM site_status"))
                            and all(x["bridge_state"] == "1" for x in r) and r, 60, desc="bridges up")
    evidence.add("all bridges reconnected", "SQL site_status", 0, status)


@pytest.mark.gate("G29", "logging outage does not stop control")
def test_g29_logging_outage_does_not_stop_control(evidence, site_reset):
    site = "site06"
    site_reset.append(site)
    lab.baseline(site)
    lab.run(["docker", "stop", "-t", "3", "orchid-historian"], record="central historian/audit DOWN")
    lab.ctl(site, "POST", "/fault/log", {"fail": True}, record="local log sink failure injected")
    try:
        lab.set_env(site, temp=23.0, hum=68.0)
        fans = lab.wait_group(site, "fan_group", "ON", 40)
        dehum = lab.wait_group(site, "dehumidifier_group", "ON", 40)
        st = lab.status(site)
        evidence.add("control continues with central + local logging unavailable", "faultctl GET /status", 0,
                     {"fan_group": fans, "dehumidifier_group": dehum, "issues": st["issues"], "log": st["log"]})
        assert "LOCAL_LOG_DEGRADED" in st["issues"] and st["log"]["dropped"] > 0
    finally:
        lab.ctl(site, "POST", "/fault/clear", {})
        lab.run(["docker", "start", "orchid-historian"], record="historian restored")
    lab.set_env(site, temp=19.0, hum=56.0)
