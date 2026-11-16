"""Bounded JSON parsing for untrusted MQTT payloads.

Rejects: oversize, invalid UTF-8, excessive nesting (pre-scanned, no recursion),
NaN/Infinity constants, malformed JSON.
"""
from __future__ import annotations

import json
from typing import Any


class PayloadError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _max_depth(text: str, limit: int) -> int:
    """Return nesting depth (stops early once limit exceeded). Ignores string contents."""
    depth = 0
    peak = 0
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > peak:
                peak = depth
                if peak > limit:
                    return peak
        elif ch in "]}":
            depth -= 1
    return peak


def _reject_constant(name: str) -> Any:
    raise PayloadError("NON_FINITE", name)


def parse_json_bounded(raw: bytes, *, max_bytes: int, max_depth: int) -> Any:
    if raw is None or len(raw) == 0:
        raise PayloadError("MALFORMED_JSON", "empty payload")
    if len(raw) > max_bytes:
        raise PayloadError("OVERSIZE", f"{len(raw)} > {max_bytes} bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PayloadError("ENCODING", "invalid utf-8") from exc
    if _max_depth(text, max_depth) > max_depth:
        raise PayloadError("TOO_DEEP", f"nesting > {max_depth}")
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except PayloadError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise PayloadError("MALFORMED_JSON", type(exc).__name__) from exc
