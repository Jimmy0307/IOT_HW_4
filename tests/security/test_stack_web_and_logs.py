"""Stack gates: HTTPS dashboard/API, freshness semantics, malicious logs, flood, audit tamper evidence."""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

import pytest

from tests import lab

pytestmark = pytest.mark.stack
RAW = lab.EVIDENCE / "raw"


def publish_event(event: dict, *, user: str = "site01-temp01", record: str | None = None) -> dict:
    return lab.probe("pub", "--host", "site01-broker", "--user", user, "--topic", "orchid/v1/site01/events",
                     "--payload", json.dumps(event), record=record)


def wait_event(event_id: str, timeout: float = 30) -> list[dict]:
    return lab.wait_until(lambda: lab.db("SELECT * FROM events WHERE site='site01' AND event_id=?", (event_id,)),
                          timeout, desc=f"event {event_id}")


@pytest.mark.gate("G03", "https dashboard reachable")
def test_g03_https_dashboard_and_api(evidence):
    import ssl
    import urllib.request

    ctx = ssl.create_default_context(cafile=str(lab.ROOT / "secrets" / "pki" / "ca.crt"))
    with urllib.request.urlopen(lab.API_BASE + "/", context=ctx, timeout=10) as resp:  # noqa: S310
        body = resp.read().decode()
        headers = dict(resp.headers)
    evidence.add("GET / over HTTPS (CA-verified, hostname localhost)", f"GET {lab.API_BASE}/", resp.status,
                 {"status": resp.status, "csp": headers.get("Content-Security-Policy"),
                  "hsts": headers.get("Strict-Transport-Security"), "has_root_div": 'id="root"' in body})
    assert resp.status == 200 and 'id="root"' in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    code, _ = lab.api("GET", "/api/overview", record="unauthenticated API call")
    assert code == 401
    code, payload = lab.api("POST", "/api/auth/login", body={"username": "viewer", "password": "wrong"},
                            record="login with wrong password")
    assert code == 401
    token = lab.login("viewer")
    code, ov = lab.api("GET", "/api/overview", token=token)
    evidence.add("overview (viewer)", "GET /api/overview", code,
                 [{k: s[k] for k in ("site_id", "status", "mode", "data_quality")} for s in ov["sites"]])
    assert code == 200 and len(ov["sites"]) == 6


@pytest.mark.gate("G26a", "rbac viewer read only operator command end to end")
def test_rbac_and_operator_command_end_to_end(evidence):
    viewer, operator = lab.login("viewer"), lab.login("operator")
    code, _ = lab.api("POST", "/api/sites/site06/commands", token=viewer,
                      body={"group": "fan_group", "desired_state": "ON", "reason": "viewer attempt"},
                      record="viewer POST command")
    assert code == 403
    code, resp = lab.api("POST", "/api/sites/site06/commands", token=operator,
                         body={"group": "fan_group", "desired_state": "ON", "reason": "operator e2e test"},
                         record="operator POST command via HTTPS")
    assert code == 202
    cid = resp["command_id"]
    row = lab.wait_until(lambda: lab.db("SELECT status, members, operator FROM commands WHERE command_id=? AND "
                                        "status='APPLIED'", (cid,)), 30, desc="ack")
    evidence.add("site ACK for dashboard command", "SQL commands", 0, row)
    assert row[0]["operator"] == "operator"
    audit = lab.wait_until(lambda: lab.db("SELECT event_type, principal FROM audit_log WHERE event_type IN "
                                          "('OPERATOR_COMMAND','AUTHZ_DENIED') AND details LIKE ?",
                                          ("%site06%",)), 20, desc="audit")
    evidence.add("operator command + authz denial audited (principal central-api)", "SQL audit_log", 0, audit)
    code, _ = lab.api("POST", "/api/sites/site06/mode", token=operator,
                      body={"mode": "AUTO", "reason": "restore"}, record="restore AUTO")
    assert code == 202


@pytest.mark.gate("G24", "retained stale state not shown fresh")
def test_g24_retained_stale_state_not_shown_fresh(evidence):
    site = "site04"
    token = lab.login("viewer")
    lab.baseline(site)
    lab.set_env(site, temp=23.0)
    lab.wait_group(site, "fan_group", "ON")

    def card():
        code, ov = lab.api("GET", "/api/overview", token=token)
        return next(s for s in ov["sites"] if s["site_id"] == site)

    fresh = lab.wait_until(lambda: (c := card())["actuators"]["fan01"]["display"] == "ON" and c, 30, desc="fresh ON")
    evidence.add("before: fresh ON", "GET /api/overview", 200,
                 {"status": fresh["status"], "fan01": fresh["actuators"]["fan01"]})
    lab.run(["docker", "pause", f"orchid-{site}-controller"], record="freeze controller (no LWT yet, TCP open)")
    try:
        stale = lab.wait_until(lambda: (c := card())["actuators"]["fan01"]["display"] == "STALE" and c, 60,
                               desc="STALE display")
        evidence.add("controller frozen: retained ON shown as STALE", "GET /api/overview", 200,
                     {"status": stale["status"], "fan01": stale["actuators"]["fan01"],
                      "group": stale["groups"]["fan_group"]})
        assert stale["actuators"]["fan01"]["state"] == "ON" and stale["actuators"]["fan01"]["fresh"] is False
        assert stale["status"] in ("UNKNOWN", "OFFLINE")
        retained = lab.probe("sub", "--host", "central-broker", "--user", "historian", "--topic",
                             f"orchid/v1/{site}/actuator/fan01/state", "--listen", "1",
                             record="broker still holds retained 'ON' (would mislead a naive UI)")
        assert retained["messages"] and retained["messages"][0]["retain"]
    finally:
        lab.run(["docker", "unpause", f"orchid-{site}-controller"], record="unfreeze controller")
    lab.wait_until(lambda: card()["status"] in ("ONLINE", "DEGRADED"), 60, desc="back online")
    lab.run(["docker", "kill", "-s", "KILL", f"orchid-{site}-controller"], record="kill controller (ungraceful)")
    try:
        off = lab.wait_until(lambda: (c := card())["status"] == "OFFLINE" and c, 60, desc="LWT OFFLINE")
        evidence.add("LWT -> OFFLINE, actuators STALE", "GET /api/overview", 200,
                     {"status": off["status"], "fan01": off["actuators"]["fan01"]})
        assert off["actuators"]["fan01"]["display"] == "STALE"
        hb = lab.db("SELECT heartbeat FROM site_status WHERE site=?", (site,), record="retained LWT heartbeat")
        assert json.loads(hb[0]["heartbeat"])["reason"] == "LWT"
    finally:
        lab.run(["docker", "start", f"orchid-{site}-controller"], record="restart controller")
    lab.wait_until(lambda: card()["status"] in ("ONLINE", "DEGRADED"), 60, desc="birth ONLINE again")
    lab.reset_site(site)


@pytest.mark.gate("G25", "log crlf injection neutralized")
def test_g25_crlf_injection(evidence):
    eid = f"g25-{uuid.uuid4().hex[:8]}"
    forged = f"g25-forged-{uuid.uuid4().hex[:6]}"
    msg = f'sensor ok\r\n{{"event_id":"{forged}","event_type":"LOGIN_SUCCESS","principal":"admin"}}\n\x1b[31mRED'
    publish_event({"event_id": eid, "event_type": "DIAGNOSTIC", "details": {"message": msg}},
                  record="device publishes event with CR/LF + forged JSON line + ANSI escape")
    rows = wait_event(eid)
    stored = json.loads(rows[0]["details"])["message"]
    evidence.add("stored event (single record, escapes visible)", "SQL events", 0, rows)
    assert "\r" not in stored and "\n" not in stored and "\x1b" not in stored
    assert "\\r\\n" in stored and "\\u001b" in stored
    assert not lab.db("SELECT 1 FROM events WHERE event_id=?", (forged,), record="forged second record absent")
    logs = lab.run(["docker", "logs", "--since", "2m", "orchid-historian"])
    assert forged not in "\n".join(l for l in logs.stdout.splitlines() if not l.startswith("{"))


@pytest.mark.gate("G26", "stored xss neutralized")
def test_g26_stored_xss_api_returns_inert_text(evidence):
    eid = f"g26-{uuid.uuid4().hex[:8]}"
    payload = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    publish_event({"event_id": eid, "event_type": "DIAGNOSTIC", "details": {"message": payload}},
                  record="device publishes XSS payload")
    wait_event(eid)
    token = lab.login("viewer")
    code, data = lab.api("GET", "/api/events?site=site01&limit=50", token=token)
    ev = next(e for e in data["events"] if e["event_id"] == eid)
    evidence.add("API returns payload as JSON string data (application/json)", "GET /api/events", code, ev)
    assert ev["details"]["message"] == payload
    proc = subprocess.run(["npm", "run", "-s", "lint:security"], cwd=lab.ROOT / "services" / "frontend",
                          capture_output=True, text=True, shell=sys.platform == "win32")
    evidence.add("frontend static scan: no dangerouslySetInnerHTML/innerHTML/eval", "npm run lint:security",
                 proc.returncode, proc.stdout + proc.stderr)
    assert proc.returncode == 0
    evidence.note("Browser rendering check: see evidence/generated/ui (Playwright) - text rendered, no script executed.")


@pytest.mark.gate("G27", "fake principal severity rejected")
def test_g27_fake_principal_and_severity(evidence):
    eid = f"g27-{uuid.uuid4().hex[:8]}"
    publish_event({"event_id": eid, "event_type": "ACTUATOR_FAILURE", "principal": "admin", "severity": "INFO",
                   "ingest_ts": "1999-01-01T00:00:00Z", "site": "site02", "details": {"message": "forged"}},
                  record="device claims principal=admin severity=INFO site=site02")
    rows = wait_event(eid)
    evidence.add("authoritative values from server", "SQL events", 0, rows)
    r = rows[0]
    assert r["principal"] == "bridge-site01" and r["site"] == "site01" and r["severity"] == "CRITICAL"
    assert not r["ingest_ts"].startswith("1999")
    drop = lab.wait_until(lambda: lab.db("SELECT * FROM events WHERE event_type='UNTRUSTED_CLAIM_DROPPED' AND "
                                         "details LIKE ?", (f"%{eid}%",)), 20, desc="claim drop record")
    evidence.add("claim drop recorded as SECURITY", "SQL events", 0, drop)
    assert drop[0]["severity"] == "SECURITY"
    publish_event({"event_id": f"{eid}-x", "event_type": "LOGIN_SUCCESS", "details": {}},
                  record="device tries a server-only event type")
    lab.wait_until(lambda: lab.db("SELECT 1 FROM events WHERE event_type='EVENT_SCHEMA_REJECTED' AND details LIKE ?",
                                  ("%EVENT_TYPE_NOT_ALLOWED%",)), 20, desc="schema reject")


@pytest.mark.gate("G28", "log flood bounded")
def test_g28_log_flood_bounded(evidence):
    tag = uuid.uuid4().hex[:6]
    flood = [{"payload": {"event_id": f"flood-{tag}-{i}", "event_type": "DIAGNOSTIC",
                          "details": {"message": "x" * 200}}, "qos": 0} for i in range(3000)]
    crit = [{"payload": {"event_id": f"crit-{tag}-{i}", "event_type": "ACTUATOR_FAILURE",
                         "details": {"message": "critical during flood"}}, "qos": 1} for i in range(5)]
    size_before = lab.run(["docker", "exec", "orchid-historian", "du", "-sk", "/data"]).stdout.split()[0]
    t0 = time.monotonic()
    res = lab.dexec_json("orchid-lab-probe", ["python", "-m", "orchid.tools.probe", "publish-many", "--host",
                                              "site01-broker", "--user", "site01-temp01", "--topic",
                                              "orchid/v1/site01/events"],
                         input=json.dumps(flood + crit), record="3000 low-priority + 5 critical events", timeout=120)
    assert res["sent"] == 3005
    time.sleep(15)
    elapsed = time.monotonic() - t0
    stored = lab.db("SELECT COUNT(*) AS n FROM events WHERE event_id LIKE ?", (f"flood-{tag}-%",))[0]["n"]
    crit_rows = lab.db("SELECT COUNT(*) AS n FROM events WHERE event_id LIKE ?", (f"crit-{tag}-%",))[0]["n"]
    summary = lab.wait_until(lambda: lab.db("SELECT details FROM events WHERE site='site01' AND "
                                            "event_type='RATE_LIMITED' ORDER BY id DESC LIMIT 3"), 30, desc="summary")
    size_after = lab.run(["docker", "exec", "orchid-historian", "du", "-sk", "/data"]).stdout.split()[0]
    health = lab.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", "orchid-historian",
                      "orchid-site01-controller", "orchid-site01-broker"]).stdout.split()
    st = lab.status("site01")
    result = {"flood_sent": 3000, "flood_stored": stored, "critical_sent": 5, "critical_stored": crit_rows,
              "elapsed_s": round(elapsed, 1), "rate_limited_summaries": summary,
              "historian_data_kb_before_after": [size_before, size_after], "health": health,
              "site01_control": {"mode": st["mode"]["mode"], "issues": st["issues"]}}
    evidence.add("flood outcome", "SQL + docker inspect", 0, result)
    bound = 20 + 5 * elapsed + 5
    assert stored <= bound, result
    assert crit_rows == 5, result
    assert health == ["healthy", "healthy", "healthy"]
    assert int(size_after) - int(size_before) < 20_000


@pytest.mark.gate("G30", "audit tampering detected")
def test_g30_audit_tamper_detected(evidence):
    RAW.mkdir(parents=True, exist_ok=True)
    snap = RAW / "audit-snapshot.db"
    py = sys.executable
    proc = lab.run([py, "scripts/verify_audit_chain.py", "--snapshot", str(snap)],
                   record="snapshot running historian DB and verify chain")
    assert proc.returncode == 0 and json.loads(proc.stdout)["result"] == "PASS", proc.stdout
    tampered = RAW / "audit-tampered.db"
    shutil.copy(snap, tampered)
    con = sqlite3.connect(tampered)
    target = con.execute("SELECT seq, severity FROM audit_log ORDER BY seq LIMIT 1 OFFSET 5").fetchone()
    con.execute("DROP TRIGGER audit_no_update")
    con.execute("UPDATE audit_log SET severity='INFO', details='{\"message\":\"nothing to see\"}' WHERE seq=?",
                (target[0],))
    con.commit()
    con.close()
    evidence.add("attacker with file access drops trigger and edits one audit row", "sqlite3 (copy)", 0,
                 {"seq": target[0], "original_severity": target[1]})
    proc = lab.run([py, "scripts/verify_audit_chain.py", "--db", str(tampered)], record="verify tampered copy")
    out = json.loads(proc.stdout)
    assert proc.returncode == 1 and out["result"] == "FAIL", out
    deleted = RAW / "audit-deleted.db"
    shutil.copy(snap, deleted)
    con = sqlite3.connect(deleted)
    con.execute("DROP TRIGGER audit_no_delete")
    con.execute("DELETE FROM audit_log WHERE seq=(SELECT seq FROM audit_log ORDER BY seq LIMIT 1 OFFSET 3)")
    con.commit()
    con.close()
    proc = lab.run([py, "scripts/verify_audit_chain.py", "--db", str(deleted)], record="verify copy with deleted row")
    assert proc.returncode == 1
    evidence.note("Tamper EVIDENCE, not prevention: whoever controls the file can still alter it; the HMAC "
                  "chain (key outside the DB) makes the alteration detectable.")
