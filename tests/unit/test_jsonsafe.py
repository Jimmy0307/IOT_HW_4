"""Bounded JSON parsing: size, depth, encoding, non-finite constants."""
from __future__ import annotations

import pytest

from orchid.core.jsonsafe import PayloadError, parse_json_bounded


def test_valid_object():
    assert parse_json_bounded(b'{"a": 1}', max_bytes=100, max_depth=3) == {"a": 1}


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"{" + b'"a":1,' * 200 + b'"b":2}', "OVERSIZE"),
        (b'{"a": {"b": {"c": {"d": {"e": 1}}}}}', "TOO_DEEP"),
        (b"[" * 900 + b"]" * 900, "OVERSIZE"),
        (b'{"a": NaN}', "NON_FINITE"),
        (b'{"a": Infinity}', "NON_FINITE"),
        (b'{"a": -Infinity}', "NON_FINITE"),
        (b'{"a": 1', "MALFORMED_JSON"),
        (b"not json", "MALFORMED_JSON"),
        (b"\xff\xfe{}", "ENCODING"),
        (b"", "MALFORMED_JSON"),
    ],
)
def test_rejections(raw, code):
    with pytest.raises(PayloadError) as exc:
        parse_json_bounded(raw, max_bytes=1024, max_depth=3)
    assert exc.value.code == code


def test_depth_scan_ignores_brackets_inside_strings():
    raw = b'{"a": "[[[[[[{{{{{{"}'
    assert parse_json_bounded(raw, max_bytes=100, max_depth=2) == {"a": "[[[[[[{{{{{{"}


def test_deep_nesting_within_size_limit_rejected_without_recursion_error():
    raw = b"[" * 5000 + b"]" * 5000
    with pytest.raises(PayloadError) as exc:
        parse_json_bounded(raw, max_bytes=20000, max_depth=4)
    assert exc.value.code == "TOO_DEEP"
