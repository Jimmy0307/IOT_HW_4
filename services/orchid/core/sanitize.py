"""Untrusted event/log sanitization.

Logs are untrusted input. This module:
* neutralizes CR/LF, C0/C1 control chars, DEL, Unicode line separators and bidi
  overrides into visible escapes (no forged second record, no terminal tricks);
* enforces a top-level field allowlist and an event_type allowlist;
* drops client-claimed authoritative fields (principal, severity, ingest_ts, site)
  and records that they were dropped;
* bounds the number and size of detail fields; nested values become inert text.
HTML is NOT stripped here: it is stored as inert text and must be escaped by
every renderer (React text nodes do this).
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

TRUNC = "...[truncated]"
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
DETAIL_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
AUTHORITATIVE_CLAIMS = ("principal", "severity", "ingest_ts", "site", "site_id")
ALLOWED_TOP_LEVEL = ("event_id", "event_type", "ts", "details")
_DANGEROUS = {0x2028, 0x2029, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}


class EventRejected(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _escape_char(ch: str) -> str:
    code = ord(ch)
    if ch == "\r":
        return "\\r"
    if ch == "\n":
        return "\\n"
    if ch == "\t":
        return "\\t"
    if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F or code in _DANGEROUS:
        return f"\\u{code:04x}"
    return ch


def neutralize_text(value: str, max_len: int) -> str:
    out = "".join(_escape_char(ch) for ch in str(value))
    if len(out) > max_len:
        out = out[:max_len] + TRUNC
    return out


def _scalar(value: Any, max_len: int) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) < 2**53 else neutralize_text(str(value), max_len)
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return neutralize_text(value, max_len)
    # Nested structures are flattened to inert, bounded text.
    return neutralize_text(json.dumps(value, ensure_ascii=True, default=str)[: max_len * 2], max_len)


def sanitize_event(
    obj: Any,
    *,
    allowed_types: Iterable[str],
    max_detail_keys: int,
    max_len: int,
) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise EventRejected("SCHEMA_INVALID", "event must be a JSON object")
    event_id = obj.get("event_id")
    if not isinstance(event_id, str) or not EVENT_ID_RE.match(event_id):
        raise EventRejected("SCHEMA_INVALID", "bad event_id")
    event_type = obj.get("event_type")
    if not isinstance(event_type, str) or event_type not in set(allowed_types):
        raise EventRejected("EVENT_TYPE_NOT_ALLOWED", neutralize_text(str(event_type), 48))

    claimed = sorted(k for k in obj if k in AUTHORITATIVE_CLAIMS)
    unknown = sorted(
        neutralize_text(k, 32) for k in obj if k not in ALLOWED_TOP_LEVEL and k not in AUTHORITATIVE_CLAIMS
    )

    raw_details = obj.get("details", {})
    if not isinstance(raw_details, dict):
        raw_details = {"value": raw_details}
    details: dict[str, Any] = {}
    truncated = False
    for key, value in raw_details.items():
        if len(details) >= max_detail_keys:
            truncated = True
            break
        if not isinstance(key, str) or not DETAIL_KEY_RE.match(key):
            truncated = True
            continue
        details[key] = _scalar(value, max_len)

    claimed_ts = obj.get("ts")
    return {
        "event_id": event_id,
        "event_type": event_type,
        "claimed_ts": neutralize_text(claimed_ts, 40) if isinstance(claimed_ts, str) else None,
        "details": details,
        "details_truncated": truncated,
        "untrusted_claims_dropped": claimed,
        "unknown_fields_dropped": unknown[:8],
    }
