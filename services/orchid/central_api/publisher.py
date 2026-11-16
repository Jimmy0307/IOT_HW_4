"""Central API MQTT publisher: operator commands (QoS1, Message Expiry) + own audit events."""
from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from orchid.core import topics
from orchid.core.config import Config
from orchid.core.mqttc import MqttSettings, PubackTracker, build_client, connect_async, publish_properties
from orchid.core.timeutil import iso, utcnow

PUBACK_TIMEOUT_S = 3.0


class PublishError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status


class MqttPublisher:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.tracker = PubackTracker()
        self._connected = threading.Event()
        self.settings = MqttSettings(host="central-broker", username="central-api", client_id="central-api",
                                     clean_start=True,
                                     session_expiry=cfg.platform.sessions.api_session_expiry_seconds)
        self.client = build_client(self.settings, on_connect=self._on_connect, on_disconnect=self._on_disconnect,
                                   tracker=self.tracker)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if not reason_code.is_failure:
            self._connected.set()

    def _on_disconnect(self, *args: Any) -> None:
        self._connected.clear()

    def start(self) -> None:
        connect_async(self.client, self.settings)

    def publish_command(self, topic: str, payload: bytes, ttl: int) -> int:
        if not self.connected:
            raise PublishError(503, "central broker unavailable")
        info = self.client.publish(topic, payload, qos=1, retain=False, properties=publish_properties(ttl))
        code = self.tracker.wait(info.mid, PUBACK_TIMEOUT_S)
        if code is None:
            raise PublishError(504, "no PUBACK from broker")
        if code >= 0x80:
            raise PublishError(502, f"broker refused publish (reason 0x{code:02x})")
        return code

    def audit_event(self, event_type: str, **details: Any) -> None:
        body = {"event_id": str(uuid.uuid4()), "event_type": event_type, "ts": iso(utcnow()), "details": details}
        # QoS1; paho buffers (bounded) while disconnected.
        self.client.publish(topics.CENTRAL_AUDIT, json.dumps(body).encode(), qos=1, retain=False)
