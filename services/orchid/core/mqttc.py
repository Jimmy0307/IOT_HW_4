"""MQTT 5 client factory: verified TLS, file-based credentials, bounded sessions.

TLS verification is never disabled: CERT_REQUIRED + hostname check against the
lab CA. There is intentionally no option to set tls_insecure.
"""
from __future__ import annotations

import os
import ssl
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

SECRETS_DIR = Path(os.environ.get("ORCHID_SECRETS_DIR", "/run/orchid-secrets"))


def read_secret(name: str, base: Path | None = None) -> str:
    path = (base or SECRETS_DIR) / name
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"secret {name} is empty")
    return value


@dataclass(frozen=True)
class MqttSettings:
    host: str
    username: str
    client_id: str
    password_file: str = "mqtt.pw"
    ca_file: str = "ca.crt"
    port: int = 8883
    keepalive: int = 30
    clean_start: bool = True
    session_expiry: int = 0
    secrets_dir: Path | None = None


def connect_properties(session_expiry: int) -> Properties:
    props = Properties(PacketTypes.CONNECT)
    props.SessionExpiryInterval = int(session_expiry)
    return props


def publish_properties(expiry_seconds: int | None = None) -> Properties | None:
    if not expiry_seconds:
        return None
    props = Properties(PacketTypes.PUBLISH)
    props.MessageExpiryInterval = int(expiry_seconds)
    return props


def tls_context(ca_path: Path) -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ca_path))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


class PubackTracker:
    """Records MQTT 5 PUBACK reason codes (e.g. 0x87 Not authorized) per message id."""

    def __init__(self) -> None:
        self._codes: dict[int, int] = {}
        self._cond = threading.Condition()
        self.denied = 0

    def on_publish(self, client: Any, userdata: Any, mid: int, reason_code: Any, properties: Any) -> None:
        value = getattr(reason_code, "value", reason_code)
        with self._cond:
            self._codes[mid] = int(value) if value is not None else 0
            if self._codes[mid] >= 0x80:
                self.denied += 1
            while len(self._codes) > 4096:
                self._codes.pop(next(iter(self._codes)))
            self._cond.notify_all()

    def wait(self, mid: int, timeout: float) -> int | None:
        with self._cond:
            self._cond.wait_for(lambda: mid in self._codes, timeout=timeout)
            return self._codes.pop(mid, None)


def build_client(
    settings: MqttSettings,
    *,
    on_connect: Callable | None = None,
    on_message: Callable | None = None,
    on_disconnect: Callable | None = None,
    will: tuple[str, bytes, int, bool] | None = None,
    tracker: PubackTracker | None = None,
) -> mqtt.Client:
    base = settings.secrets_dir or SECRETS_DIR
    client = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=settings.client_id,
        protocol=mqtt.MQTTv5,
    )
    client.username_pw_set(settings.username, read_secret(settings.password_file, base))
    client.tls_set_context(tls_context(base / settings.ca_file))
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    client.max_queued_messages_set(2000)  # bounded client-side buffer while disconnected
    if will:
        topic, payload, qos, retain = will
        client.will_set(topic, payload, qos=qos, retain=retain)
    if on_connect:
        client.on_connect = on_connect
    if on_message:
        client.on_message = on_message
    if on_disconnect:
        client.on_disconnect = on_disconnect
    if tracker:
        client.on_publish = tracker.on_publish
    return client


def connect_async(client: mqtt.Client, settings: MqttSettings) -> None:
    client.connect_async(
        settings.host,
        settings.port,
        keepalive=settings.keepalive,
        clean_start=settings.clean_start,
        properties=connect_properties(settings.session_expiry),
    )
    client.loop_start()
