"""Least-privilege ACL model invariants."""
from __future__ import annotations

from orchid.core import acl_model
from orchid.historian.main import SUBSCRIPTIONS

SITES = [f"site0{i}" for i in range(1, 7)]
SENSORS = {"temp01": "temperature", "humidity01": "humidity"}
GROUPS = {"fan_group": ["fan01", "fan02"], "dehumidifier_group": ["dehumidifier01", "dehumidifier02"]}
SPECS = {s: acl_model.SiteSpec(SENSORS, GROUPS) for s in SITES}


def all_principals(test_principals=True):
    out = acl_model.central_principals(SPECS, test_principals=test_principals)
    for s in SITES:
        out += acl_model.site_principals(s, SENSORS, test_principals=test_principals and s in ("site01", "site02"))
    return out


def test_default_acl_access_is_deny_all():
    assert acl_model.DEFAULT_ACL_ACCESS == {
        "publishClientSend": False, "publishClientReceive": False, "subscribe": False, "unsubscribe": False,
    }


def test_no_bare_hash_grant_and_no_admin():
    for p in all_principals():
        for acl in p.acls():
            assert acl["topic"] not in ("#", "+/#", "orchid/v1/#")
            assert not acl["topic"].startswith("$CONTROL")
        assert "admin" not in p.username


def test_every_production_principal_is_bound_to_its_client_id():
    for p in all_principals(test_principals=False):
        assert p.clientid, p.username
    doc = acl_model.dynsec_document(all_principals(False), {p.username: "$7$x" for p in all_principals(False)})
    assert all("clientid" in c for c in doc["clients"])


def test_test_principals_are_optional_and_prefixed():
    names = {p.username for p in all_principals(True)} - {p.username for p in all_principals(False)}
    assert names and all(n.startswith("probe-") for n in names)


def test_bridge_grants_are_concrete_and_scoped_to_own_site():
    for p in acl_model.central_principals(SPECS, test_principals=False):
        if p.username.startswith("bridge-"):
            site = p.username.removeprefix("bridge-")
            for acl in p.acls():
                assert acl["topic"].startswith(f"orchid/v1/{site}/"), acl
                assert "+" not in acl["topic"] and "#" not in acl["topic"], acl


def test_bridge_never_forwards_hash_and_inbound_is_commands_only():
    for s in SITES:
        topics = acl_model.bridge_outbound_topics(s) + acl_model.bridge_inbound_topics(s)
        assert all("#" not in t for t in topics)
        assert acl_model.bridge_inbound_topics(s) == tuple(
            f"orchid/v1/{s}/control/{t}/command" for t in ("fan_group", "dehumidifier_group", "mode"))


def test_sensors_may_publish_only_their_own_telemetry():
    p = next(x for x in acl_model.site_principals("site01", SENSORS) if x.username == "site01-temp01")
    assert p.publish == ("orchid/v1/site01/sensor/temp01/telemetry/temperature",)
    assert p.subscribe == ()


def test_historian_acl_equals_its_subscriptions():
    hist = next(p for p in acl_model.central_principals(SPECS) if p.username == "historian")
    assert set(hist.subscribe) == {f for f, _ in SUBSCRIPTIONS}
    assert hist.publish == ()


def test_dynsec_document_shape():
    principals = acl_model.site_principals("site01", SENSORS)
    doc = acl_model.dynsec_document(principals, {p.username: "$7$x" for p in principals})
    assert doc["defaultACLAccess"]["publishClientReceive"] is False
    assert {c["username"] for c in doc["clients"]} == {p.username for p in principals}
    assert all(len(c["roles"]) == 1 for c in doc["clients"])
