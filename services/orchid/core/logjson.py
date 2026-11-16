"""Structured JSON logging that can never take down the caller.

* One JSON object per line (json.dumps escapes CR/LF -> no forged records).
* No string concatenation of untrusted data into log lines.
* Any failure in a sink is swallowed and counted (logging outage != control outage).
* Secret-looking keys are redacted defensively.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from collections import deque
from typing import Any, Callable

from orchid.core.timeutil import iso, utcnow

REDACT_KEYS = {"password", "passwd", "secret", "token", "jwt", "authorization", "hmac_key", "private_key"}


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if str(k).lower() in REDACT_KEYS else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact(v) for v in obj]
    return obj


class SafeJsonLogger:
    def __init__(self, component: str, file_path: str | None = None, max_bytes: int = 1_000_000,
                 backups: int = 3, stream: Any = None) -> None:
        self.component = component
        self.dropped = 0
        self.degraded = False
        self.fail_injected = False
        self._lock = threading.Lock()
        self._stream = stream or sys.stdout
        self._path = file_path
        self._max_bytes = max_bytes
        self._backups = backups
        self.critical_buffer: deque[dict[str, Any]] = deque(maxlen=200)
        if file_path:
            try:
                os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
            except OSError:
                self.degraded = True

    def _write(self, line: str, sink: Callable[[str], None]) -> None:
        try:
            if self.fail_injected:
                raise OSError("injected local log failure")
            sink(line)
        except Exception:  # noqa: BLE001 - logging must never raise into control code
            self.dropped += 1
            self.degraded = True

    def log(self, level: str, event: str, **fields: Any) -> None:
        record = {"ts": iso(utcnow()), "level": level, "component": self.component, "event": event,
                  **_redact(fields)}
        try:
            line = json.dumps(record, ensure_ascii=True, default=str, separators=(",", ":"))
        except Exception:  # noqa: BLE001
            self.dropped += 1
            return
        if level in ("CRITICAL", "SECURITY"):
            self.critical_buffer.append(record)  # bounded local buffer for security-critical events
        with self._lock:
            self._write(line, self._emit_stream)
            if self._path:
                self._write(line, self._emit_file)

    def _emit_stream(self, line: str) -> None:
        self._stream.write(line + "\n")
        self._stream.flush()

    def _emit_file(self, line: str) -> None:
        assert self._path is not None
        if os.path.exists(self._path) and os.path.getsize(self._path) > self._max_bytes:
            for i in range(self._backups - 1, 0, -1):
                src = f"{self._path}.{i}"
                if os.path.exists(src):
                    os.replace(src, f"{self._path}.{i + 1}")
            os.replace(self._path, f"{self._path}.1")
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def info(self, event: str, **f: Any) -> None:
        self.log("INFO", event, **f)

    def warning(self, event: str, **f: Any) -> None:
        self.log("WARNING", event, **f)

    def error(self, event: str, **f: Any) -> None:
        self.log("ERROR", event, **f)

    def critical(self, event: str, **f: Any) -> None:
        self.log("CRITICAL", event, **f)
