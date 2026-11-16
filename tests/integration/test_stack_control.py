"""Stack gates: six-site aggregation, AUTO group actuation, command lifecycle, arbitration, TTL."""
from __future__ import annotations

import json
import time
from datetime import timedelta

import pytest

from tests import lab

pytestmark = pytest.mark.stack
SITES = [f"site0{i}" for i in range(1, 7)]


@pytest.fixture()
def site_reset():
    used: list[str] = []
    yield used
    for site in used:
        try:
            lab.reset_site(site)
        except AssertionError:
            pass


@pytest.mark.gate("G10", "six sites telemetry available")
def test_g10_six_sites_online_and_aggregated(evidence):
    def ready():
        rows = lab.db("SELECT site, metric, COUNT(*) AS n, MAX(gateway_ingest_ts) AS last FROM validated_telemetry "
                      "WHERE validation_result='VALIDATED' AND gateway_ingest_ts >= ? GROUP BY site, metric",
                      (lab.iso(lab.now_utc() - timedelta(seconds=20)),))
        return rows if len(rows) == 12 else None

    rows = lab.wait_until(ready, 60, desc="12 fresh (site, metric) series")
    evidence.add("fresh VALIDATED telemetry per site/metric (last 20 s, central historian)", "SQL", 0, rows)
    status = lab.db("SELECT site, hb_status, bridge_state FROM site_status ORDER BY site",
                    record="site heartbeat (LWT/birth) + bridge state at central")
    assert [r["site"] for r in status] == SITES
    assert all(r["hb_status"] == "ONLINE" and r["bridge_state"] == "1" for r in status), status
    raw = lab.db("SELECT site, COUNT(*) AS n FROM raw_telemetry GROUP BY site ORDER BY site",
                 record="raw telemetry rows per site")
    assert len(raw) == 6


@pytest.mark.gate("G14", "temperature high fan group")
def test_g14_temperature_high_turns_on_both_fans(evidence, site_reset):
    site = "site02"
    site_reset.append(site)
    lab.baseline(site)
    t0 = lab.iso(lab.now_utc())
    lab.set_env(site, temp=23.0)
    lab.sim(site, "GET", "/status", record="simulator setpoint (temperature 23 C, ramped)")
    on = lab.wait_group(site, "fan_group", "ON")
    st = lab.status(site)
    evidence.add("controller groups after temperature rise", "faultctl GET /status", 0,
                 {"groups": st["groups"], "mode": st["mode"]["mode"]})
    assert on == {"fan01": "ON", "fan02": "ON"}
    assert st["groups"]["dehumidifier_group"]["members"] == {"dehumidifier01": "OFF", "dehumidifier02": "OFF"}
    ack = lab.wait_until(lambda: lab.db(
        "SELECT command_id, requested, status, members, source, reason, acked_at FROM commands WHERE site=? AND "
        "grp='fan_group' AND requested='ON' AND status='APPLIED' AND source='AUTO_RULE' AND acked_at >= ?",
        (site, t0)), 30, desc="AUTO ack at central")
    evidence.add("AUTO group ACK received centrally", "SQL commands", 0, ack)
    assert json.loads(ack[0]["members"]) == {"fan01": "ON", "fan02": "ON"} and ack[0]["reason"] == "TEMP_HIGH"


@pytest.mark.gate("G15", "humidity high dehumidifier group")
def test_g15_humidity_high_turns_on_both_dehumidifiers(evidence, site_reset):
    site = "site03"
    site_reset.append(site)
    lab.baseline(site)
    t0 = lab.iso(lab.now_utc())
    lab.set_env(site, hum=68.0)
    on = lab.wait_group(site, "dehumidifier_group", "ON")
    st = lab.status(site)
    evidence.add("controller groups after humidity rise", "faultctl GET /status", 0, st["groups"])
    assert on == {"dehumidifier01": "ON", "dehumidifier02": "ON"}
    assert st["groups"]["fan_group"]["members"] == {"fan01": "OFF", "fan02": "OFF"}
    ack = lab.wait_until(lambda: lab.db(
        "SELECT command_id, status, members, reason FROM commands WHERE site=? AND grp='dehumidifier_group' AND "
        "requested='ON' AND status='APPLIED' AND acked_at >= ?", (site, t0)), 30, desc="dehum ack")
    evidence.add("AUTO dehumidifier ACK at central", "SQL commands", 0, ack)
    assert ack[0]["reason"] == "HUMIDITY_HIGH"


@pytest.mark.gate("G17", "command ack state lifecycle")
def test_g17_remote_command_ack_and_observed_state(evidence, site_reset):
    site = "site01"
    site_reset.append(site)
    lab.baseline(site)
    body = lab.command_body("ON", ttl=30)
    lab.send_command(site, "fan_group", body, expiry=30, record="central-api publishes fan_group ON (QoS1, expiry 30s)")
    row = lab.wait_until(lambda: lab.db("SELECT * FROM commands WHERE command_id=? AND status='APPLIED'",
                                        (body["command_id"],)), 30, desc="APPLIED ack")
    evidence.add("command row: SENT -> APPLIED with member states", "SQL commands", 0, row)
    assert json.loads(row[0]["members"]) == {"fan01": "ON", "fan02": "ON"} and row[0]["source"] == "REMOTE"
    states = lab.wait_until(lambda: (r := lab.db(
        "SELECT device_id, state, quality, observed_at FROM actuator_state WHERE site=? AND grp='fan_group'", (site,)))
        and all(x["state"] == "ON" for x in r) and r, 20, desc="observed state ON at central")
    evidence.add("observed actuator state (retained, with observed_at/quality)", "SQL actuator_state", 0, states)
    retained = lab.probe("sub", "--host", "central-broker", "--user", "historian", "--topic",
                         f"orchid/v1/{site}/actuator/+/state", "--listen", "1.5",
                         record="new subscriber receives RETAINED state")
    fan_msgs = [m for m in retained["messages"] if "/fan0" in m["topic"] and m["retain"]]
    assert fan_msgs and all(json.loads(m["payload"])["state"] == "ON" for m in fan_msgs)
    st = lab.status(site)
    assert st["mode"]["mode"] == "MANUAL_OVERRIDE" and st["mode"]["override"]["operator"] == "stack-test"
    evidence.add("site mode after remote command", "faultctl GET /status", 0, st["mode"])


@pytest.mark.gate("G19", "duplicate command idempotent")
def test_g19_duplicate_command_is_idempotent(evidence, site_reset):
    site = "site01"
    site_reset.append(site)
    lab.baseline(site)
    body = lab.command_body("ON", ttl=60)
    lab.send_command(site, "dehumidifier_group", body, record="first delivery")
    lab.wait_until(lambda: lab.db("SELECT 1 FROM commands WHERE command_id=? AND status='APPLIED'",
                                  (body["command_id"],)), 30, desc="first APPLIED")
    lab.send_command(site, "dehumidifier_group", body, record="second delivery (same command_id)")
    lab.send_command(site, "dehumidifier_group", body, record="third delivery (same command_id)")
    like = f"%{body['command_id']}%"
    dup = lab.wait_until(lambda: (r := lab.db(
        "SELECT event_type, COUNT(*) AS n FROM events WHERE site=? AND details LIKE ? GROUP BY event_type",
        (site, like))) and any(x["event_type"] == "COMMAND_DUPLICATE" and x["n"] >= 2 for x in r) and r,
        30, desc="duplicate events")
    evidence.add("events per type for this command_id", "SQL events", 0, dup)
    counts = {r["event_type"]: r["n"] for r in dup}
    assert counts.get("COMMAND_APPLIED") == 1, counts
    row = lab.db("SELECT status, detail FROM commands WHERE command_id=?", (body["command_id"],),
                 record="command row keeps original APPLIED result")
    assert row[0]["status"] == "APPLIED" and "duplicate" in (row[0]["detail"] or "")


@pytest.mark.gate("G20", "manual override arbitration")
def test_g20_override_beats_auto_and_interlock_beats_override(evidence, site_reset):
    site = "site04"
    site_reset.append(site)
    lab.baseline(site)
    lab.send_command(site, "mode", lab.command_body(mode="MANUAL_OVERRIDE", ttl_seconds=180),
                     record="mode MANUAL_OVERRIDE (ttl 180 s)")
    off = lab.command_body("OFF")
    lab.send_command(site, "fan_group", off, record="operator holds fan_group OFF")
    lab.set_env(site, temp=24.0)
    lab.wait_until(lambda: (lab.status(site)["sensors"]["temperature"]["last_value"] or 0) > 23.0, 30,
                   desc="temperature above T_high")
    time.sleep(6)
    st = lab.status(site)
    evidence.add("AUTO wants ON, override holds OFF", "faultctl GET /status", 0,
                 {"mode": st["mode"], "fan_group": st["groups"]["fan_group"]})
    assert st["groups"]["fan_group"]["members"] == {"fan01": "OFF", "fan02": "OFF"}
    assert st["groups"]["fan_group"]["source"] in ("REMOTE", "MANUAL_OVERRIDE")
    # Hard safety interlock outranks the operator override.
    lab.set_env(site, temp=34.0)
    lab.wait_until(lambda: lab.status(site)["interlock_active"], 60, desc="interlock active")
    lab.wait_group(site, "fan_group", "ON", 20)
    off2 = lab.command_body("OFF")
    lab.send_command(site, "fan_group", off2, record="operator tries fan_group OFF during interlock")
    rej = lab.wait_until(lambda: lab.db("SELECT status, detail FROM commands WHERE command_id=? AND status!='SENT'",
                                        (off2["command_id"],)), 30, desc="rejection ack")
    evidence.add("remote OFF rejected by HARD_SAFETY_INTERLOCK", "SQL commands", 0, rej)
    assert rej[0]["status"] == "REJECTED" and rej[0]["detail"] == "INTERLOCK_ACTIVE"
    st = lab.status(site)
    assert st["groups"]["fan_group"]["members"] == {"fan01": "ON", "fan02": "ON"}
    assert st["groups"]["fan_group"]["source"] == "HARD_SAFETY_INTERLOCK"
    lab.set_env(site, temp=19.0)


@pytest.mark.gate("G21", "override ttl returns auto")
def test_g21_override_ttl_returns_to_auto(evidence, site_reset):
    site = "site04"
    site_reset.append(site)
    lab.baseline(site)
    t0 = lab.iso(lab.now_utc())
    lab.send_command(site, "mode", lab.command_body(mode="MANUAL_OVERRIDE", ttl_seconds=8),
                     record="MANUAL_OVERRIDE with ttl 8 s")
    lab.wait_until(lambda: lab.status(site)["mode"]["mode"] == "MANUAL_OVERRIDE", 20, desc="override active")
    ov = lab.status(site)["mode"]["override"]
    evidence.add("override carries operator/reason/created_at/expires_at", "faultctl GET /status", 0, ov)
    assert {"operator", "reason", "created_at", "expires_at"} <= set(ov)
    lab.wait_until(lambda: lab.status(site)["mode"]["mode"] == "AUTO", 30, desc="TTL -> AUTO")
    ev = lab.wait_until(lambda: lab.db(
        "SELECT event_type, details, ingest_ts FROM events WHERE site=? AND event_type IN "
        "('MODE_CHANGED','OVERRIDE_EXPIRED') AND ingest_ts >= ? ORDER BY id", (site, t0))
        if any("OVERRIDE_TTL_EXPIRED" in r["details"] for r in lab.db(
            "SELECT details FROM events WHERE site=? AND event_type='MODE_CHANGED' AND ingest_ts >= ?", (site, t0)))
        else None, 30, desc="audited TTL transition")
    evidence.add("audited mode transitions", "SQL events", 0, ev)
    audit = lab.db("SELECT seq, event_type, principal, severity FROM audit_log WHERE site=? AND "
                   "event_type IN ('MODE_CHANGED','OVERRIDE_EXPIRED') AND ingest_ts >= ?", (site, t0),
                   record="same transitions in HMAC audit chain")
    assert len(audit) >= 3
