"""Central API: freshness semantics, AuthN/AuthZ, command publishing, XSS-safe output."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from orchid.central_api import status
from orchid.central_api.app import create_app
from orchid.core import topics
from orchid.core.timeutil import iso, utcnow
from orchid.historian.db import HistorianDB
from orchid.historian.ingest import Ingestor

JWT_SECRET = "test-jwt-secret-test-jwt-secret-0123456789"


def pw_hash(pw: str) -> str:
    salt = b"s" * 16
    d = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 1000)
    return f"pbkdf2_sha256$1000${base64.b64encode(salt).decode()}${base64.b64encode(d).decode()}"


class FakePublisher:
    def __init__(self):
        self.sent = []
        self.audit = []
        self.connected = True

    def publish_command(self, topic, payload, ttl):
        self.sent.append((topic, payload, ttl))
        return 0

    def audit_event(self, event_type, **details):
        self.audit.append((event_type, details))


@pytest.fixture()
def api(cfg, tmp_path):
    db_path = tmp_path / "h.db"
    writer = HistorianDB(db_path)
    ing = Ingestor(cfg, writer, b"k" * 32)
    users = {"viewer": {"role": "viewer", "password_hash": pw_hash("view-pw")},
             "operator": {"role": "operator", "password_hash": pw_hash("op-pw")}}
    pub = FakePublisher()
    app = create_app(cfg, db_path=db_path, users=users, jwt_secret=JWT_SECRET, publisher=pub)
    client = TestClient(app)
    return client, ing, pub


def login(client, user, pw):
    r = client.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------------- pure freshness logic ----------------
def test_retained_on_but_stale_is_not_shown_as_on():
    now = utcnow()
    row = {"state": "ON", "observed_at": iso(now - timedelta(seconds=300)), "quality": "GOOD"}
    view = status.actuator_view(row, site_status="ONLINE", now=now, stale_s=30)
    assert view["display"] == "STALE" and view["fresh"] is False and view["state"] == "ON"


def test_fresh_state_requires_site_online():
    now = utcnow()
    row = {"state": "ON", "observed_at": iso(now), "quality": "GOOD"}
    assert status.actuator_view(row, site_status="ONLINE", now=now, stale_s=30)["display"] == "ON"
    assert status.actuator_view(row, site_status="OFFLINE", now=now, stale_s=30)["display"] == "STALE"
    assert status.actuator_view(row, site_status="UNKNOWN", now=now, stale_s=30)["display"] == "STALE"
    assert status.actuator_view(None, site_status="ONLINE", now=now, stale_s=30)["display"] == "UNKNOWN"


@pytest.mark.parametrize(
    "hb,bridge,age,expected",
    [
        (None, None, 0, "UNKNOWN"),
        ({"status": "ONLINE", "health": "OK"}, "1", 1, "ONLINE"),
        ({"status": "ONLINE", "health": "DEGRADED"}, "1", 1, "DEGRADED"),
        ({"status": "OFFLINE"}, "1", 1, "OFFLINE"),
        ({"status": "ONLINE", "health": "OK"}, "0", 1, "OFFLINE"),
        ({"status": "ONLINE", "health": "OK"}, "1", 120, "UNKNOWN"),
    ],
)
def test_site_status(hb, bridge, age, expected):
    now = utcnow()
    row = None
    if hb is not None or bridge is not None:
        row = {"heartbeat": json.dumps({**(hb or {}), "ts": iso(now - timedelta(seconds=age))}) if hb else None,
               "hb_ts": iso(now - timedelta(seconds=age)) if hb else None, "bridge_state": bridge}
    assert status.site_status(row, now=now, stale_s=20) == expected


# ---------------- auth ----------------
def test_login_and_wrong_password(api):
    client, _, pub = api
    assert client.post("/api/auth/login", json={"username": "viewer", "password": "nope"}).status_code == 401
    assert ("LOGIN_FAILURE", {"username": "viewer"}) in [(e, {"username": d.get("username")}) for e, d in pub.audit]
    headers = login(client, "viewer", "view-pw")
    assert client.get("/api/me", headers=headers).json()["role"] == "viewer"


def test_unauthenticated_and_tampered_tokens_rejected(api):
    client, _, _ = api
    assert client.get("/api/overview").status_code == 401
    forged = jwt.encode({"sub": "operator", "role": "operator", "exp": 9999999999,
                         "iss": "orchid-central-api", "aud": "orchid-dashboard"}, "wrong-secret-wrong-secret-0123456789",
                        algorithm="HS256")
    assert client.get("/api/overview", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    none_alg = jwt.encode({"sub": "operator", "role": "operator"}, key="", algorithm="none")
    assert client.get("/api/overview", headers={"Authorization": f"Bearer {none_alg}"}).status_code == 401


def test_expired_token_rejected(api):
    client, _, _ = api
    expired = jwt.encode({"sub": "operator", "role": "operator", "exp": 1, "iat": 0, "iss": "orchid-central-api",
                          "aud": "orchid-dashboard"}, JWT_SECRET, algorithm="HS256")
    assert client.get("/api/overview", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_login_rate_limited(api, cfg):
    client, _, _ = api
    codes = [client.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code
             for _ in range(cfg.platform.web.login_rate_limit_per_minute + 3)]
    assert codes[-1] == 429


def test_viewer_cannot_command(api):
    client, _, pub = api
    headers = login(client, "viewer", "view-pw")
    r = client.post("/api/sites/site01/commands", headers=headers,
                    json={"group": "fan_group", "desired_state": "ON", "reason": "test"})
    assert r.status_code == 403
    assert not pub.sent
    assert any(e == "AUTHZ_DENIED" for e, _ in pub.audit)


def test_operator_command_published_with_expiry(api, cfg):
    client, _, pub = api
    headers = login(client, "operator", "op-pw")
    r = client.post("/api/sites/site02/commands", headers=headers,
                    json={"group": "dehumidifier_group", "desired_state": "ON", "reason": "humid"})
    assert r.status_code == 202, r.text
    topic, payload, ttl = pub.sent[-1]
    assert topic == topics.group_command("site02", "dehumidifier_group")
    body = json.loads(payload)
    assert body["operator"] == "operator" and body["desired_state"] == "ON"
    assert ttl == cfg.platform.command.default_ttl_seconds
    assert {"command_id", "issued_at", "expires_at", "reason"} <= set(body)


@pytest.mark.parametrize("body", [
    {"group": "fan_group", "desired_state": "MAYBE", "reason": "x"},
    {"group": "../../#", "desired_state": "ON", "reason": "x"},
    {"group": "fan_group", "desired_state": "ON", "reason": "<script>"},
    {"group": "fan_group", "desired_state": "ON", "reason": "x", "ttl_seconds": 99999},
])
def test_command_input_validation(api, body):
    client, _, pub = api
    headers = login(client, "operator", "op-pw")
    assert client.post("/api/sites/site01/commands", headers=headers, json=body).status_code == 422
    assert not pub.sent


def test_unknown_site_rejected(api):
    client, _, _ = api
    headers = login(client, "operator", "op-pw")
    r = client.post("/api/sites/site99/commands", headers=headers,
                    json={"group": "fan_group", "desired_state": "ON", "reason": "x"})
    assert r.status_code == 404


def test_mode_command(api):
    client, _, pub = api
    headers = login(client, "operator", "op-pw")
    r = client.post("/api/sites/site01/mode", headers=headers,
                    json={"mode": "MANUAL_OVERRIDE", "reason": "inspection", "ttl_seconds": 120})
    assert r.status_code == 202
    assert json.loads(pub.sent[-1][1])["mode"] == "MANUAL_OVERRIDE"


# ---------------- data views ----------------
def test_overview_and_xss_payload_returned_as_plain_json_text(api):
    client, ing, _ = api
    ev = {"event_id": "x1", "event_type": "DIAGNOSTIC", "details": {"message": "<script>alert(1)</script>"}}
    ing.handle(topics.events("site01"), json.dumps(ev).encode())
    headers = login(client, "viewer", "view-pw")
    overview = client.get("/api/overview", headers=headers)
    assert overview.status_code == 200
    assert len(overview.json()["sites"]) == 6
    r = client.get("/api/events?site=site01", headers=headers)
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["events"][0]["details"]["message"] == "<script>alert(1)</script>"


def test_site_detail_and_system(api):
    client, ing, _ = api
    headers = login(client, "viewer", "view-pw")
    assert client.get("/api/sites/site01", headers=headers).status_code == 200
    assert client.get("/api/sites/nope", headers=headers).status_code == 404
    system = client.get("/api/system", headers=headers).json()
    assert {"broker", "security", "audit", "commands", "sites_connected"} <= set(system)
