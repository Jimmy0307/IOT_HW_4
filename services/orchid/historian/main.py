"""Historian / audit consumer process (central OT network).

* Persistent but bounded MQTT 5 session (catches up after a short outage).
* Two bounded queues: critical (alerts, audit, commands, acks, health, broker
  logs) and normal (telemetry, state, site events). A flood on the normal path
  cannot starve the critical path; overflow is dropped and counted.
* Single writer thread owns the SQLite connection.
"""
from __future__ import annotations

import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from orchid.core import topics
from orchid.core.acl_model import HISTORIAN_FILTERS
from orchid.core.config import load_config
from orchid.core.logjson import SafeJsonLogger
from orchid.core.mqttc import MqttSettings, build_client, connect_async, read_secret
from orchid.core.timeutil import iso, utcnow
from orchid.historian.db import HistorianDB
from orchid.historian.ingest import Ingestor

DB_PATH = Path(os.environ.get("ORCHID_DB_PATH", "/data/historian.db"))
HEALTH_FILE = Path("/tmp/orchid-health")
SUBSCRIPTIONS = list(HISTORIAN_FILTERS)  # identical to the historian's dynsec ACL
NORMAL_KINDS = {"telemetry", "gateway_telemetry", "state", "events"}
FULL_VERIFY_S = 600.0
INCREMENTAL_VERIFY_S = 30.0


def is_normal(topic: str) -> bool:
    """Critical lane: alerts, central audit, commands, acks, heartbeat, bridge state.
    Normal lane: telemetry, gateway telemetry, state, site events, broker $SYS logs/metrics."""
    if topic.startswith("$SYS/"):
        return True
    parsed = topics.parse(topic)
    return parsed is not None and parsed.kind in NORMAL_KINDS


class Historian:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.log = SafeJsonLogger("historian")
        q = self.cfg.platform.audit.queue
        self.critical: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=q.critical_max)
        self.normal: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=q.normal_max)
        self.dropped = {"critical": 0, "normal": 0}
        self.stop = threading.Event()
        self.connected = threading.Event()
        self.settings = MqttSettings(
            host="central-broker", username="historian", client_id="historian", clean_start=False,
            session_expiry=self.cfg.platform.sessions.historian_session_expiry_seconds)
        self.client = build_client(self.settings, on_connect=self._on_connect, on_message=self._on_message,
                                   on_disconnect=self._on_disconnect)

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code.is_failure:
            self.log.error("MQTT_CONNECT_FAILED", reason=str(reason_code))
            return
        client.subscribe(SUBSCRIPTIONS)
        self.connected.set()
        self.log.info("MQTT_CONNECTED", session_present=bool(getattr(flags, "session_present", False)))

    def _on_disconnect(self, *args: Any) -> None:
        self.connected.clear()
        self.log.warning("MQTT_DISCONNECTED")

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        lane = "normal" if is_normal(msg.topic) else "critical"
        target = self.normal if lane == "normal" else self.critical
        try:
            target.put_nowait((msg.topic, bytes(msg.payload)))
        except queue.Full:
            self.dropped[lane] += 1

    def _drain(self, ing: Ingestor, db: HistorianDB, budget: int = 500) -> int:
        handled = 0
        with db.transaction():
            for source in (self.critical, self.normal):  # critical lane always first
                while handled < budget:
                    try:
                        topic, payload = source.get_nowait()
                    except queue.Empty:
                        break
                    ing.handle(topic, payload)
                    handled += 1
        return handled

    def run(self) -> None:
        db = HistorianDB(DB_PATH)
        key = read_secret("audit_hmac_key").encode()
        ing = Ingestor(self.cfg, db, key)
        result = ing.verify_audit()
        self.log.info("AUDIT_CHAIN_STARTUP_CHECK", ok=result.ok, rows=result.checked)
        connect_async(self.client, self.settings)
        self.last_periodic = self.last_prune = self.last_verify = 0.0
        self.last_full_verify = time.monotonic()
        while not self.stop.is_set():
            try:
                self._iteration(ing, db)
            except Exception as exc:  # noqa: BLE001 - e.g. sqlite I/O error: keep running, resync chain head
                self.log.error("HISTORIAN_LOOP_ERROR", error=type(exc).__name__, detail=str(exc)[:200])
                ing.resync()
                time.sleep(1.0)
        self.client.disconnect()
        self.client.loop_stop()
        db.close()

    def _iteration(self, ing: Ingestor, db: HistorianDB) -> None:
        if self._drain(ing, db) == 0:
            time.sleep(0.05)
        now = time.monotonic()
        if now - self.last_periodic >= 2.0:
            self.last_periodic = now
            ts = iso(utcnow())
            with db.transaction():
                ing.periodic()
                db.set_metric("queue_dropped_critical", self.dropped["critical"], ts)
                db.set_metric("queue_dropped_normal", self.dropped["normal"], ts)
                db.set_metric("historian_mqtt_connected", int(self.connected.is_set()), ts)
                db.set_metric("historian_heartbeat", ts, ts)
            try:
                HEALTH_FILE.write_text(str(time.time()))
            except OSError:
                pass
        if now - self.last_prune >= 60.0:
            self.last_prune = now
            ing.prune()
        if now - self.last_verify >= INCREMENTAL_VERIFY_S:
            self.last_verify = now
            self._verify(ing, db, full=now - self.last_full_verify >= FULL_VERIFY_S)

    def _verify(self, ing: Ingestor, db: HistorianDB, *, full: bool) -> None:
        """Incremental (new rows only) every 30 s; full chain from genesis every 10 min."""
        if full:
            self.last_full_verify = time.monotonic()
        res = ing.verify_audit() if full else ing.verify_incremental()
        ts = iso(utcnow())
        rows = db.query("SELECT COUNT(*) AS n FROM audit_log")[0]["n"]
        db.set_metric("audit_integrity", "PASS" if res.ok else "FAIL", ts)
        db.set_metric("audit_rows", rows, ts)
        db.set_metric("audit_verify_mode", "full" if full else "incremental", ts)
        db.set_metric("audit_head", res.head[:16], ts)
        if not res.ok:
            self.log.critical("AUDIT_CHAIN_BROKEN", index=res.first_bad_index, reason=res.reason)

def main() -> int:
    hist = Historian()
    signal.signal(signal.SIGTERM, lambda *_: hist.stop.set())
    signal.signal(signal.SIGINT, lambda *_: hist.stop.set())
    hist.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
