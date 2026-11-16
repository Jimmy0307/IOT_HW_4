"""SQLite historian / audit store (lab scale).

Single writer (historian process). Readers (central-api) open read-only.
audit_log has append-only triggers (defense in depth) and an HMAC chain
(tamper EVIDENCE). Someone with file access can still drop triggers; the chain
makes that detectable - it does not prevent it.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS raw_telemetry(
  id INTEGER PRIMARY KEY, central_ingest_ts TEXT NOT NULL, site TEXT NOT NULL, device_id TEXT NOT NULL,
  metric TEXT NOT NULL, payload TEXT NOT NULL, parse_ok INTEGER NOT NULL, value REAL, device_ts TEXT, seq INTEGER);
CREATE TABLE IF NOT EXISTS validated_telemetry(
  id INTEGER PRIMARY KEY, central_ingest_ts TEXT NOT NULL, gateway_ingest_ts TEXT, site TEXT NOT NULL,
  device_id TEXT NOT NULL, metric TEXT NOT NULL, value REAL, unit TEXT, device_ts TEXT, seq INTEGER,
  quality TEXT NOT NULL, validation_result TEXT NOT NULL, control_eligible INTEGER NOT NULL,
  late INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_vt_site_metric ON validated_telemetry(site, metric, id);
CREATE TABLE IF NOT EXISTS actuator_state(
  site TEXT NOT NULL, device_id TEXT NOT NULL, grp TEXT, state TEXT, commanded TEXT, quality TEXT,
  observed_at TEXT, source TEXT, central_ingest_ts TEXT, PRIMARY KEY(site, device_id));
CREATE TABLE IF NOT EXISTS commands(
  command_id TEXT PRIMARY KEY, site TEXT NOT NULL, grp TEXT NOT NULL, requested TEXT, operator TEXT,
  reason TEXT, issued_at TEXT, expires_at TEXT, status TEXT, detail TEXT, members TEXT, source TEXT,
  mode TEXT, acked_at TEXT, created_ts TEXT, updated_ts TEXT);
CREATE INDEX IF NOT EXISTS ix_cmd_site ON commands(site, created_ts);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, ingest_ts TEXT NOT NULL, site TEXT NOT NULL, principal TEXT NOT NULL,
  kind TEXT NOT NULL, event_type TEXT NOT NULL, severity TEXT NOT NULL, event_id TEXT NOT NULL,
  claimed_ts TEXT, details TEXT NOT NULL, UNIQUE(site, event_id));
CREATE INDEX IF NOT EXISTS ix_events_site ON events(site, id);
CREATE TABLE IF NOT EXISTS audit_log(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, ingest_ts TEXT NOT NULL,
  principal TEXT NOT NULL, site TEXT NOT NULL, event_type TEXT NOT NULL, severity TEXT NOT NULL,
  details TEXT NOT NULL, prev_hmac TEXT NOT NULL, hmac TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TABLE IF NOT EXISTS site_status(
  site TEXT PRIMARY KEY, heartbeat TEXT, hb_status TEXT, hb_ts TEXT, hb_ingest_ts TEXT,
  bridge_state TEXT, bridge_ts TEXT);
CREATE TABLE IF NOT EXISTS metrics(key TEXT PRIMARY KEY, value TEXT, updated_ts TEXT);
"""


class HistorianDB:
    def __init__(self, path: Path | str, readonly: bool = False, timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.readonly = readonly
        if readonly:
            uri = f"file:{self.path.as_posix()}?mode=ro"
            self.con = sqlite3.connect(uri, uri=True, timeout=timeout, check_same_thread=False,
                                       isolation_level=None)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.con = sqlite3.connect(self.path, timeout=timeout, check_same_thread=False, isolation_level=None)
            self.con.executescript(SCHEMA)
        self.con.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._in_tx = False

    def close(self) -> None:
        self.con.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if self._in_tx:
                yield
                return
            self.con.execute("BEGIN IMMEDIATE")
            self._in_tx = True
            try:
                yield
                self.con.execute("COMMIT")
            except BaseException:
                self.con.execute("ROLLBACK")
                raise
            finally:
                self._in_tx = False

    def execute(self, sql: str, args: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.con.execute(sql, tuple(args))

    def query(self, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.con.execute(sql, tuple(args)).fetchall()]

    def last_hmac(self) -> str | None:
        row = self.query("SELECT hmac FROM audit_log ORDER BY seq DESC LIMIT 1")
        return row[0]["hmac"] if row else None

    def audit_rows(self) -> Iterator[dict[str, Any]]:
        cur = self.con.execute("SELECT * FROM audit_log ORDER BY seq")
        for row in cur:
            yield dict(row)

    def set_metric(self, key: str, value: Any, ts: str) -> None:
        self.execute("INSERT INTO metrics(key, value, updated_ts) VALUES(?,?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ts=excluded.updated_ts",
                     (key, str(value), ts))
