"""Log/event sanitization (untrusted input) and HMAC audit chain."""
from __future__ import annotations

import json

import pytest

from orchid.core.audit_chain import GENESIS, chain_append, verify_chain
from orchid.core.sanitize import EventRejected, neutralize_text, sanitize_event
from orchid.historian.policy import ALLOWED_EVENT_TYPES, severity_for
from orchid.historian.ratelimit import TokenBucket


def test_crlf_and_control_chars_neutralized():
    out = neutralize_text("ok\r\n{\"event_type\":\"ADMIN\"}\x00\x1b[31m ", 512)
    assert "\r" not in out and "\n" not in out and "\x00" not in out and "\x1b" not in out
    assert " " not in out
    assert "\\r\\n" in out
    assert "\\u001b" in out


def test_bidi_override_neutralized():
    assert "‮" not in neutralize_text("abc‮def", 100)


def test_truncation():
    out = neutralize_text("a" * 1000, 50)
    assert len(out) <= 64
    assert out.endswith("[truncated]")


def test_html_kept_as_inert_text():
    # Sanitizer stores text; rendering layer must escape. We assert it is not altered
    # into something executable and is preserved for forensic value.
    assert neutralize_text("<script>alert(1)</script>", 100) == "<script>alert(1)</script>"


def base_event(**kw):
    ev = {"event_id": "e-1", "event_type": "DIAGNOSTIC", "ts": "2026-11-17T00:00:00+08:00",
          "details": {"message": "hello"}}
    ev.update(kw)
    return ev


def test_fake_principal_and_severity_dropped():
    ev = sanitize_event(base_event(principal="admin", severity="INFO", ingest_ts="1999", site="site02"),
                        allowed_types=ALLOWED_EVENT_TYPES, max_detail_keys=16, max_len=512)
    assert "principal" not in ev and "severity" not in ev
    assert sorted(ev["untrusted_claims_dropped"]) == ["ingest_ts", "principal", "severity", "site"]


def test_unknown_event_type_rejected():
    with pytest.raises(EventRejected) as exc:
        sanitize_event(base_event(event_type="ROOTKIT_INSTALLED"), allowed_types=ALLOWED_EVENT_TYPES,
                       max_detail_keys=16, max_len=512)
    assert exc.value.code == "EVENT_TYPE_NOT_ALLOWED"


@pytest.mark.parametrize("bad_id", ["", "a" * 65, "id\nx", "../../etc", None, 5])
def test_bad_event_id_rejected(bad_id):
    with pytest.raises(EventRejected):
        sanitize_event(base_event(event_id=bad_id), allowed_types=ALLOWED_EVENT_TYPES,
                       max_detail_keys=16, max_len=512)


def test_details_nested_values_flattened_to_text_and_keys_bounded():
    details = {f"k{i}": i for i in range(40)}
    details["nested"] = {"a": [1, 2]}
    ev = sanitize_event(base_event(details=details), allowed_types=ALLOWED_EVENT_TYPES,
                        max_detail_keys=16, max_len=512)
    assert len(ev["details"]) <= 16
    assert ev["details_truncated"] is True
    for value in ev["details"].values():
        assert isinstance(value, (str, int, float, bool)) or value is None


def test_crlf_injection_in_message_cannot_create_second_record():
    msg = 'boot ok\r\n{"event_type":"ADMIN_LOGIN","principal":"root"}'
    ev = sanitize_event(base_event(details={"message": msg}), allowed_types=ALLOWED_EVENT_TYPES,
                        max_detail_keys=16, max_len=512)
    line = json.dumps(ev)
    assert len(line.splitlines()) == 1
    assert "\n" not in ev["details"]["message"]


def test_server_severity_policy_ignores_claims():
    assert severity_for("ACTUATOR_PARTIAL_FAILURE") == "CRITICAL"
    assert severity_for("DIAGNOSTIC") == "INFO"
    assert severity_for("NOT_A_TYPE") == "SECURITY"


def test_audit_chain_detects_tampering():
    key = b"k" * 32
    rows = []
    prev = GENESIS
    for i in range(5):
        row = {"event_id": f"e{i}", "ingest_ts": f"2026-11-17T00:00:0{i}Z", "principal": "bridge-site01",
               "site": "site01", "event_type": "DIAGNOSTIC", "severity": "INFO",
               "details": {"message": f"m{i}"}}
        row = chain_append(key, prev, row)
        prev = row["hmac"]
        rows.append(row)
    assert verify_chain(key, rows).ok

    tampered = [dict(r) for r in rows]
    tampered[2] = dict(tampered[2], severity="CRITICAL")
    res = verify_chain(key, tampered)
    assert not res.ok and res.first_bad_index == 2

    deleted = rows[:2] + rows[3:]
    assert not verify_chain(key, deleted).ok

    assert not verify_chain(b"x" * 32, rows).ok


def test_token_bucket():
    clock = [0.0]
    bucket = TokenBucket(rate=5, burst=10, clock=lambda: clock[0])
    allowed = sum(bucket.allow() for _ in range(100))
    assert allowed == 10
    clock[0] += 1.0
    assert sum(bucket.allow() for _ in range(100)) == 5
