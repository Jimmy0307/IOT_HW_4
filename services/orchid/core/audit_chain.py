"""HMAC hash chain for tamper EVIDENCE (not tamper prevention).

hmac_i = HMAC-SHA256(key, prev_hmac_i || "|" || canonical(event_i))

Anyone holding the DB file can delete/alter rows, but cannot recompute a valid
chain without the HMAC key, so modifications are detected by verify_chain().
Truncation of the newest rows is only detectable against an externally recorded
head (count + last hmac), which verify_audit_chain.py prints for that purpose.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

GENESIS = "0" * 64
CHAIN_FIELDS = ("event_id", "ingest_ts", "principal", "site", "event_type", "severity", "details")


def canonical(event: Mapping[str, Any]) -> bytes:
    body = {k: event[k] for k in CHAIN_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def compute_hmac(key: bytes, prev: str, event: Mapping[str, Any]) -> str:
    return hmac.new(key, prev.encode() + b"|" + canonical(event), hashlib.sha256).hexdigest()


def chain_append(key: bytes, prev: str, event: Mapping[str, Any]) -> dict[str, Any]:
    row = {k: event[k] for k in CHAIN_FIELDS}
    row["prev_hmac"] = prev
    row["hmac"] = compute_hmac(key, prev, row)
    return row


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    first_bad_index: int | None = None
    reason: str = ""
    head: str = GENESIS


def verify_chain(key: bytes, rows: Iterable[Mapping[str, Any]]) -> VerifyResult:
    prev = GENESIS
    count = 0
    for index, row in enumerate(rows):
        count += 1
        if row.get("prev_hmac") != prev:
            return VerifyResult(False, count, index, "prev_hmac link broken (row inserted/deleted/reordered)", prev)
        expected = compute_hmac(key, prev, row)
        if not hmac.compare_digest(expected, str(row.get("hmac", ""))):
            return VerifyResult(False, count, index, "hmac mismatch (row content modified or wrong key)", prev)
        prev = row["hmac"]
    return VerifyResult(True, count, None, "", prev)
