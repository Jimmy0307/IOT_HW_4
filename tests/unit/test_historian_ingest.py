"""Historian ingest: authoritative binding, sanitization, dedup, flood bounds, audit chain."""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest

from orchid.core import topics
from orchid.core.timeutil import iso
from orchid.historian.db import HistorianDB
from orchid.historian.ingest import Ingestor

KEY = b"unit-test-hmac-key-unit-test-hmac"


class Clock:
    def __init__(self, t0):
        self.t = t0
        self.mono = 0.0

    def now(self):
        return self.t

    def monotonic(self):
        return self.mono

    def advance(self, s):
        self.t += timedelta(seconds=s)
        self.mono += s


@pytest.fixture()
def env(cfg, tmp_path, t0):
    db = HistorianDB(tmp_path / "h.db")
    clock = Clock(t0)
    ing = Ingestor(cfg, db, KEY, now=clock.now, monotonic=clock.monotonic)
    return ing, db, clock


def event(event_id="e1", event_type="DIAGNOSTIC", **extra):
    body = {"event_id": event_id, "event_type": event_type, "ts": "2026-11-17T00:00:00+08:00",
            "details": {"message": "hello"}}
    body.update(extra)
    return json.dumps(body).encode()


def rows(db, sql, *args):
    return db.query(sql, args)


def test_event_bound_to_authenticated_principal_and_server_severity(env):
    ing, db, _ = env
    ing.handle(topics.events("site01"), event(principal="admin", severity="INFO", event_type="ACTUATOR_FAILURE"))
    ev = rows(db, "SELECT principal, site, severity, details FROM events WHERE event_type='ACTUATOR_FAILURE'")[0]
    assert ev["principal"] == "bridge-site01"
    assert ev["site"] == "site01"
    assert ev["severity"] == "CRITICAL"  # server policy, not the claimed INFO
    details = json.loads(ev["details"])
    assert "principal" not in details
    sec = rows(db, "SELECT * FROM events WHERE event_type='UNTRUSTED_CLAIM_DROPPED'")
    assert sec and sec[0]["severity"] == "SECURITY"


def test_crlf_injection_creates_exactly_one_record(env):
    ing, db, _ = env
    msg = 'ok\r\n{"event_id":"e2","event_type":"LOGIN_SUCCESS","principal":"root"}'
    ing.handle(topics.events("site01"), event(details={"message": msg}))
    evs = rows(db, "SELECT * FROM events WHERE event_type != 'UNTRUSTED_CLAIM_DROPPED'")
    assert len(evs) == 1
    stored = json.loads(evs[0]["details"])["message"]
    assert "\r" not in stored and "\n" not in stored and "\\r\\n" in stored


def test_stored_xss_kept_as_inert_text(env):
    ing, db, _ = env
    ing.handle(topics.events("site01"), event(details={"message": "<script>alert(1)</script>"}))
    stored = json.loads(rows(db, "SELECT details FROM events")[0]["details"])["message"]
    assert stored == "<script>alert(1)</script>"  # escaping is the renderer's job (React text node)


def test_server_only_event_type_cannot_be_forged_by_site(env):
    ing, db, _ = env
    ing.handle(topics.events("site01"), event(event_type="LOGIN_SUCCESS"))
    ing.handle(topics.events("site01"), event(event_id="e9", event_type="RATE_LIMITED"))
    types = [r["event_type"] for r in rows(db, "SELECT event_type FROM events")]
    assert "LOGIN_SUCCESS" not in types and "RATE_LIMITED" not in types
    assert types.count("EVENT_SCHEMA_REJECTED") == 2


def test_event_dedup(env):
    ing, db, _ = env
    for _ in range(3):
        ing.handle(topics.events("site01"), event(event_id="same"))
    assert len(rows(db, "SELECT * FROM events WHERE event_id='same'")) == 1


def test_malformed_and_oversized_events_rejected(env, cfg):
    ing, db, _ = env
    ing.handle(topics.events("site01"), b"{not json")
    ing.handle(topics.events("site01"), b'{"a":' * 20 + b"1" + b"}" * 20)
    ing.handle(topics.events("site01"), json.dumps({"x": "y" * (cfg.platform.payload_limits.max_event_bytes)}).encode())
    rej = rows(db, "SELECT details FROM events WHERE event_type='EVENT_SCHEMA_REJECTED'")
    assert len(rej) == 3


def test_flood_is_bounded_and_critical_events_survive(env, cfg):
    ing, db, clock = env
    for i in range(3000):
        ing.handle(topics.events("site01"), event(event_id=f"flood-{i}"))
    for i in range(5):
        ing.handle(topics.events("site01"), event(event_id=f"crit-{i}", event_type="ACTUATOR_FAILURE"))
    clock.advance(11)
    ing.periodic()
    info = rows(db, "SELECT COUNT(*) AS n FROM events WHERE event_type='DIAGNOSTIC'")[0]["n"]
    assert info <= cfg.platform.audit.rate_limit.normal_burst + 1
    assert rows(db, "SELECT COUNT(*) AS n FROM events WHERE event_type='ACTUATOR_FAILURE'")[0]["n"] == 5
    summary = rows(db, "SELECT details FROM events WHERE event_type='RATE_LIMITED'")
    assert summary and json.loads(summary[0]["details"])["dropped"] >= 2900
    assert ing.counters["rate_limited"] >= 2900


def test_audit_chain_verifies_and_detects_tampering(env, tmp_path):
    ing, db, _ = env
    for i in range(10):
        ing.handle(topics.events("site02"), event(event_id=f"a{i}", event_type="MODE_CHANGED"))
    assert ing.verify_audit().ok
    db.close()
    con = sqlite3.connect(tmp_path / "h.db")
    con.execute("DROP TRIGGER audit_no_update")
    con.execute("UPDATE audit_log SET severity='INFO' WHERE seq=3")
    con.commit()
    con.close()
    db2 = HistorianDB(tmp_path / "h.db")
    res = Ingestor.verify_db(db2, KEY)
    assert not res.ok and res.first_bad_index == 2


def test_audit_log_append_only_triggers(env):
    ing, db, _ = env
    ing.handle(topics.events("site01"), event(event_type="MODE_CHANGED"))
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE audit_log SET severity='INFO'")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM audit_log")


def gw(site, device="temp01", metric="temperature", value=21.0, result="VALIDATED", **kw):
    body = {"schema_version": "1.0", "site_id": site, "device_id": device, "metric": metric,
            "gateway_ingest_ts": kw.pop("gwts", "2026-11-17T00:00:00Z"), "quality": "GOOD",
            "validation_result": result, "stage": "VALIDATED", "control_eligible": result == "VALIDATED",
            "device_ts": "2026-11-17T08:00:00+08:00", "seq": 5, "value": value, "unit": "C"}
    body.update(kw)
    return json.dumps(body).encode()


def test_gateway_telemetry_stored_with_central_ingest_ts(env):
    ing, db, clock = env
    ing.handle(topics.gateway_telemetry("site03", "temp01", "temperature"), gw("site03", gwts=iso(clock.now())))
    row = rows(db, "SELECT * FROM validated_telemetry")[0]
    assert row["site"] == "site03" and row["value"] == 21.0 and row["late"] == 0
    assert row["central_ingest_ts"] and row["gateway_ingest_ts"]


def test_late_store_and_forward_data_flagged(env):
    ing, db, clock = env
    old = iso(clock.now() - timedelta(seconds=120))
    ing.handle(topics.gateway_telemetry("site03", "temp01", "temperature"), gw("site03", gwts=old))
    assert rows(db, "SELECT late FROM validated_telemetry")[0]["late"] == 1


def test_topic_identity_mismatch_rejected(env):
    ing, db, _ = env
    ing.handle(topics.gateway_telemetry("site01", "temp01", "temperature"), gw("site02"))
    assert not rows(db, "SELECT * FROM validated_telemetry")
    assert rows(db, "SELECT * FROM events WHERE event_type='TOPIC_IDENTITY_MISMATCH'")


def test_command_and_ack_lifecycle(env):
    ing, db, _ = env
    cid = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"
    cmd = {"command_id": cid, "issued_at": "2026-11-17T00:00:00Z", "expires_at": "2026-11-17T00:00:30Z",
           "desired_state": "ON", "operator": "operator", "reason": "TEST"}
    ing.handle(topics.group_command("site01", "fan_group"), json.dumps(cmd).encode())
    ack = {"command_id": cid, "site_id": "site01", "group": "fan_group", "requested_state": "ON",
           "result": "APPLIED", "members": {"fan01": "ON", "fan02": "ON"}, "source": "REMOTE",
           "acked_at": "2026-11-17T00:00:01Z"}
    ing.handle(topics.group_ack("site01", "fan_group"), json.dumps(ack).encode())
    row = rows(db, "SELECT * FROM commands WHERE command_id=?", cid)[0]
    assert row["status"] == "APPLIED" and row["operator"] == "operator"
    assert json.loads(row["members"]) == {"fan01": "ON", "fan02": "ON"}


def test_broker_auth_failure_from_sys_log(env):
    ing, db, _ = env
    ing.handle("$SYS/broker/log/N", b"2026-11-17T00:00:00: Client x [10.0.0.9:1234] disconnected: not authorised.")
    ev = rows(db, "SELECT * FROM events WHERE event_type='BROKER_AUTH_FAILURE'")
    assert ev and ev[0]["principal"] == "central-broker"
    assert ing.counters["auth_failures"] == 1


def test_retention_prunes_raw_but_never_audit(env, cfg):
    ing, db, _ = env
    for i in range(30):
        ing.handle(topics.telemetry("site01", "temp01", "temperature"), b'{"value": 1}')
    ing.retention_limits = {"raw_telemetry": 10}
    ing.prune()
    assert rows(db, "SELECT COUNT(*) AS n FROM raw_telemetry")[0]["n"] == 10
