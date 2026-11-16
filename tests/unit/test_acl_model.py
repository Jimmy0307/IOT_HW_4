"""Least-privilege ACL model invariants."""
from __future__ import annotations

from orchid.core import acl_model

SITES = [f"site0{i}" for i in range(1, 7)]
SENSORS = {"temp01": "temperature", "humidity01": "humidity"}


def all_principals():
    out = acl_model.central_principals(SITES)
    for s in SITES:
        out += acl_model.site_principals(s, SENSORS)
    return out


def test_default_acl_access_is_deny_all():
    assert acl_model.DEFAULT_ACL_ACCESS == {
        "publishClientSend": False, "publishClientReceive": False, "subscribe": False, "unsubscribe": False,
    }


def test_no_bare_hash_grant_and_no_admin():
    for p in all_principals():
        for acl in p.acls():
            assert acl["topic"] not in ("#", "+/#", "$CONTROL/#")
            assert not acl["topic"].startswith("$CONTROL")
        assert "admin" not in p.username


def test_bridge_scoped_to_own_site():
    for p in acl_model.central_principals(SITES):
        if p.username.startswith("bridge-"):
            site = p.username.removeprefix("bridge-")
            for acl in p.acls():
                assert acl["topic"].startswith(f"orchid/v1/{site}/"), acl


def test_bridge_does_not_forward_hash():
    for s in SITES:
        topics = acl_model.bridge_outbound_topics(s) + acl_model.bridge_inbound_topics(s)
        assert all("#" not in t for t in topics)
        assert acl_model.bridge_inbound_topics(s) == (f"orchid/v1/{s}/control/+/command",)


def test_sensors_cannot_publish_commands_or_other_devices():
    p = next(x for x in acl_model.site_principals("site01", SENSORS) if x.username == "site01-temp01")
    assert p.publish == ("orchid/v1/site01/sensor/temp01/telemetry/temperature", "orchid/v1/site01/events")
    assert p.subscribe == ()


def test_dynsec_document_shape():
    principals = acl_model.site_principals("site01", SENSORS)
    doc = acl_model.dynsec_document(principals, {p.username: "$7$x" for p in principals})
    assert doc["defaultACLAccess"]["publishClientReceive"] is False
    assert {c["username"] for c in doc["clients"]} == {p.username for p in principals}
    assert all(len(c["roles"]) == 1 for c in doc["clients"])
