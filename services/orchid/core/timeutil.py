"""Time helpers. All server-side timestamps are timezone-aware UTC."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_aware(value: str) -> datetime:
    """Parse ISO-8601; reject naive timestamps."""
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("timestamp must be a short ISO-8601 string")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset")
    return dt
