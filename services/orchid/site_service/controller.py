"""Site controller process: MQTT I/O around SiteEngine.

* Single processing thread owns the engine (no locks around control state).
* paho network thread only enqueues inbound messages (bounded queue).
* Bounded MQTT 5 session (clean_start=False, Session Expiry from config) so
  queued QoS1 commands survive a short restart but never live forever.
* LWT retained OFFLINE; retained ONLINE heartbeat after every (re)connect.
* Loopback-only internal HTTP for status and fault injection.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from orchid.core import internal_http, topics
from orchid.core.config import load_config
from orchid.core.logjson import SafeJsonLogger
from orchid.core.mqttc import MqttSettings, PubackTracker, build_client, connect_async, publish_properties
from orchid.core.timeutil import utcnow
from orchid.site_service.engine import Out, SiteEngine

INBOUND_MAX = 5000
COMMAND_QUEUE_MAX = 500
HEALTH_FILE = Path("/tmp/orchid-health")
DATA_DIR = Path(os.environ.get("ORCHID_DATA_DIR", "/data"))


class ControllerRunner:
    def __init__(self, site_id: str) -> None:
        self.cfg = load_config()
        self.site_id = site_id
        self.log = SafeJsonLogger(f"{site_id}-controller", str(DATA_DIR / "site-controller.log"))
        self.engine = SiteEngine(self.cfg, site_id, log=self.log)
        self.inbound: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=INBOUND_MAX)
        # Commands get their own queue, drained first, so a telemetry flood cannot delay or drop them.
        self.commands: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=COMMAND_QUEUE_MAX)
        self.loop_errors = 0
        self.publish_failures = 0
        self.control: queue.Queue[tuple[str, dict[str, Any], queue.Queue]] = queue.Queue(maxsize=100)
        self.inbound_dropped = 0
        self.connected = threading.Event()
        self.stop = threading.Event()
        self.tracker = PubackTracker()
        self._status: dict[str, Any] = {}
        self.settings = MqttSettings(
            host=f"{site_id}-broker",
            username=f"{site_id}-controller",
            client_id=f"{site_id}-controller",
            clean_start=False,
            session_expiry=self.cfg.platform.sessions.controller_session_expiry_seconds,
        )
        lwt_topic, lwt_payload = self.engine.lwt()
        self.client = build_client(self.settings, on_connect=self._on_connect, on_message=self._on_message,
                                   on_disconnect=self._on_disconnect, will=(lwt_topic, lwt_payload, 1, True),
                                   tracker=self.tracker)

    # ---------------- paho callbacks (network thread) ----------------
    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code.is_failure:
            self.log.error("MQTT_CONNECT_FAILED", reason=str(reason_code))
            return
        subs = [(topics.telemetry(self.site_id, "+", "+"), 1), (topics.group_command(self.site_id, "+"), 1)]
        client.subscribe(subs)
        self.connected.set()
        self.log.info("MQTT_CONNECTED", session_present=bool(getattr(flags, "session_present", False)))
        self._enqueue_control("reconnected", {})

    def _on_disconnect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        self.connected.clear()
        self.log.warning("MQTT_DISCONNECTED", reason=str(reason_code))

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        target = self.commands if msg.topic.endswith("/command") else self.inbound
        try:
            target.put_nowait((msg.topic, bytes(msg.payload)))
        except queue.Full:
            self.inbound_dropped += 1

    # ---------------- internal control (loopback HTTP thread) ----------------
    def _enqueue_control(self, action: str, body: dict[str, Any]) -> queue.Queue:
        reply: queue.Queue = queue.Queue(maxsize=1)
        try:
            self.control.put_nowait((action, body, reply))
        except queue.Full:
            reply.put({"error": "busy"})
        return reply

    def _http(self, method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path == "/status":
            return 200, self._status
        if method == "POST" and path in ("/fault/actuator", "/fault/log", "/fault/clear"):
            reply = self._enqueue_control(path, body)
            try:
                return 200, reply.get(timeout=5)
            except queue.Empty:
                return 504, {"error": "timeout"}
        return 404, {"error": "not found"}

    def _handle_control(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        if action == "/fault/actuator":
            self.engine.set_actuator_fault(str(body["member"]), body.get("fault"))
            return {"ok": True, "faults": self.engine.bank.faults()}
        if action == "/fault/log":
            self.log.fail_injected = bool(body.get("fail", True))
            return {"ok": True, "log_fail_injected": self.log.fail_injected}
        if action == "/fault/clear":
            for member in self.engine.bank.members():
                self.engine.set_actuator_fault(member, None)
            self.log.fail_injected = False
            self.log.degraded = False
            return {"ok": True}
        return {"ok": True}

    # ---------------- publish ----------------
    def _publish(self, outs: list[Out]) -> None:
        for out in outs:
            try:
                info = self.client.publish(out.topic, out.encode(), qos=out.qos, retain=out.retain,
                                           properties=publish_properties(out.expiry))
                if info.rc != 0:  # e.g. MQTT_ERR_QUEUE_FULL while offline for a long time
                    self.publish_failures += 1
            except Exception as exc:  # noqa: BLE001 - publishing failure must not stop control
                self.publish_failures += 1
                self.log.error("PUBLISH_FAILED", topic=out.topic, error=type(exc).__name__)

    def _safe(self, fn: Any, *args: Any) -> list[Out]:
        """No single message or tick may take the control loop down."""
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            self.loop_errors += 1
            self.log.error("CONTROL_LOOP_ERROR", where=getattr(fn, "__name__", "?"), error=type(exc).__name__,
                           detail=str(exc)[:200])
            return []

    # ---------------- dedup persistence ----------------
    def _dedup_path(self) -> Path:
        return DATA_DIR / "dedup.json"

    def _load_dedup(self) -> None:
        try:
            ids = json.loads(self._dedup_path().read_text())
            if isinstance(ids, list):
                self.engine.load_dedup([str(i) for i in ids])
        except (OSError, ValueError):
            pass

    def _save_dedup(self) -> None:
        try:
            tmp = self._dedup_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(self.engine.dedup_ids()))
            os.replace(tmp, self._dedup_path())
        except OSError:
            self.log.warning("DEDUP_PERSIST_FAILED")

    # ---------------- main loop ----------------
    def run(self) -> None:
        self._load_dedup()
        internal_http.serve(self._http)
        startup = self.engine.startup(utcnow())
        connect_async(self.client, self.settings)
        tick_s = self.cfg.platform.timing.tick_seconds
        next_tick = time.monotonic()
        pending_startup: list[Out] | None = startup
        saved_generation = self.engine.gate.generation
        while not self.stop.is_set():
            while not self.commands.empty():
                topic, payload = self.commands.get_nowait()
                self._publish(self._safe(self.engine.on_message, topic, payload, utcnow()))
            try:
                topic, payload = self.inbound.get(timeout=max(0.0, min(0.1, next_tick - time.monotonic())))
                self._publish(self._safe(self.engine.on_message, topic, payload, utcnow()))
            except queue.Empty:
                pass
            while not self.control.empty():
                action, body, reply = self.control.get_nowait()
                if action == "reconnected":
                    self._publish(pending_startup or self.engine.republish(utcnow()))
                    pending_startup = None
                    continue
                try:
                    reply.put_nowait(self._handle_control(action, body))
                except (ValueError, KeyError) as exc:
                    reply.put_nowait({"error": str(exc)})
            if self.engine.gate.generation != saved_generation:
                saved_generation = self.engine.gate.generation
                self._save_dedup()
            if time.monotonic() >= next_tick:
                now = utcnow()
                self._publish(self._safe(self.engine.tick, now))
                try:
                    snap = self.engine.snapshot(now)
                except Exception:  # noqa: BLE001
                    snap = {"error": "snapshot failed"}
                snap["mqtt_connected"] = self.connected.is_set()
                snap["inbound_dropped"] = self.inbound_dropped
                snap["puback_denied"] = self.tracker.denied
                snap["loop_errors"] = self.loop_errors
                snap["publish_failures"] = self.publish_failures
                self._status = snap
                try:
                    HEALTH_FILE.write_text(str(time.time()))
                except OSError:
                    pass
                next_tick = time.monotonic() + tick_s
        self.shutdown()

    def shutdown(self) -> None:
        topic, _ = self.engine.lwt()
        graceful = json.dumps({"status": "OFFLINE", "site_id": self.site_id, "reason": "SHUTDOWN"}).encode()
        try:
            info = self.client.publish(topic, graceful, qos=1, retain=True)
            info.wait_for_publish(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        self.client.disconnect()
        self.client.loop_stop()


def main() -> int:
    site_id = os.environ.get("ORCHID_SITE_ID", "")
    if not topics.SITE_RE.match(site_id):
        print(json.dumps({"level": "ERROR", "event": "INVALID_SITE_ID"}), file=sys.stderr)
        return 64
    runner = ControllerRunner(site_id)
    signal.signal(signal.SIGTERM, lambda *_: runner.stop.set())
    signal.signal(signal.SIGINT, lambda *_: runner.stop.set())
    runner.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
