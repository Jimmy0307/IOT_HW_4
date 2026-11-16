"""Field device simulator: one temperature + one humidity sensor per site.

Each sensor is its own MQTT principal (site01-temp01, site01-humidity01).
Values follow a slow per-site profile (so the dashboard shows variation) unless
a test pins a setpoint. Faults are injectable ONLY via the loopback-only
internal HTTP interface (docker exec + orchid.tools.faultctl).
"""
from __future__ import annotations

import json
import math
import os
import random
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from orchid.core import internal_http, topics
from orchid.core.config import load_config
from orchid.core.logjson import SafeJsonLogger
from orchid.core.mqttc import MqttSettings, build_client, connect_async
from orchid.core.timeutil import TPE, utcnow

FAULTS = (
    "malformed_json", "invalid_unit", "null_value", "string_value", "nan_value", "infinity_value",
    "stale_ts", "future_ts", "duplicate_seq", "backward_seq", "spike", "frozen", "offline",
    "oversize", "deep_json", "identity_spoof", "naive_ts",
)
ONE_SHOT = {"spike"}
HEALTH_FILE = Path("/tmp/orchid-health")
TEMP_BASE = [19.4, 20.2, 20.8, 19.8, 21.2, 19.6]
HUM_BASE = [58.0, 61.0, 63.5, 57.0, 60.0, 64.0]
RAMP = {"temperature": 1.0, "humidity": 4.0}  # per second, below plausibility limits
NOISE = {"temperature": 0.05, "humidity": 0.2}


@dataclass
class SensorSim:
    site_id: str
    device_id: str
    metric: str
    unit: str
    index: int
    value: float = 0.0
    setpoint: float | None = None
    fault: str | None = None
    fault_remaining: int | None = None
    seq: int = field(default_factory=lambda: int(time.time() * 1000))
    frozen_value: float | None = None

    def __post_init__(self) -> None:
        self.value = self.profile(time.time())

    def profile(self, t: float) -> float:
        if self.metric == "temperature":
            return TEMP_BASE[self.index % 6] + 0.8 * math.sin(2 * math.pi * t / 900 + self.index)
        return HUM_BASE[self.index % 6] + 2.5 * math.sin(2 * math.pi * t / 1200 + self.index * 1.7)

    def step(self, dt: float) -> None:
        target = self.setpoint if self.setpoint is not None else self.profile(time.time())
        delta = max(-RAMP[self.metric] * dt, min(RAMP[self.metric] * dt, target - self.value))
        self.value += delta

    def reading(self) -> float:
        noisy = self.value + random.uniform(-NOISE[self.metric], NOISE[self.metric])  # noqa: S311 - simulation
        if self.metric == "humidity":
            noisy = min(100.0, max(0.0, noisy))
        return round(noisy, 2)

    def _consume_fault(self) -> str | None:
        fault = self.fault
        if fault and self.fault_remaining is not None:
            self.fault_remaining -= 1
            if self.fault_remaining <= 0:
                self.fault, self.fault_remaining = None, None
        return fault

    def message(self) -> bytes | None:
        """Build the next payload (or None when offline)."""
        fault = self._consume_fault()
        if fault == "offline":
            return None
        now = utcnow().astimezone(TPE)
        self.seq += 1
        body: dict[str, Any] = {
            "schema_version": "1.0", "site_id": self.site_id, "device_id": self.device_id,
            "device_ts": now.isoformat(timespec="milliseconds"), "seq": self.seq, "metric": self.metric,
            "value": self.reading(), "unit": self.unit,
        }
        if fault == "frozen":
            self.frozen_value = self.frozen_value if self.frozen_value is not None else body["value"]
            body["value"] = self.frozen_value
        else:
            self.frozen_value = None
        if fault == "spike":
            body["value"] = round(body["value"] + (15.0 if self.metric == "temperature" else 35.0), 2)
            body["value"] = min(body["value"], 100.0) if self.metric == "humidity" else body["value"]
        elif fault == "invalid_unit":
            body["unit"] = "F" if self.metric == "temperature" else "g/m3"
        elif fault == "null_value":
            body["value"] = None
        elif fault == "string_value":
            body["value"] = "20abc"
        elif fault == "stale_ts":
            body["device_ts"] = (now - timedelta(seconds=120)).isoformat()
        elif fault == "future_ts":
            body["device_ts"] = (now + timedelta(seconds=120)).isoformat()
        elif fault == "naive_ts":
            body["device_ts"] = now.replace(tzinfo=None).isoformat()
        elif fault == "duplicate_seq":
            self.seq -= 1
            body["seq"] = self.seq
        elif fault == "backward_seq":
            body["seq"] = self.seq - 1000
        elif fault == "identity_spoof":
            body["site_id"] = "site02" if self.site_id != "site02" else "site03"
        elif fault == "oversize":
            body["pad"] = "x" * 4096
        raw = json.dumps(body).encode()
        if fault == "malformed_json":
            return raw[: len(raw) // 2]
        if fault in ("nan_value", "infinity_value"):
            token = b"NaN" if fault == "nan_value" else b"Infinity"
            return raw.replace(json.dumps(body["value"]).encode(), token, 1)
        if fault == "deep_json":
            return b'{"a":' * 50 + b"1" + b"}" * 50
        return raw


class Simulator:
    def __init__(self, site_id: str) -> None:
        self.cfg = load_config()
        self.site_id = site_id
        self.site = self.cfg.sites[site_id]
        self.log = SafeJsonLogger(f"{site_id}-sim")
        index = int(site_id[-2:]) - 1
        self.sensors: dict[str, SensorSim] = {}
        self.clients: dict[str, Any] = {}
        self.settings: dict[str, MqttSettings] = {}
        for device, metric in self.site.sensors.items():
            unit = self.site.sensor_contract[metric].unit
            self.sensors[device] = SensorSim(site_id, device, metric, unit, index)
            st = MqttSettings(host=f"{site_id}-broker", username=f"{site_id}-{device}",
                              client_id=f"{site_id}-{device}", password_file=f"{device}.pw")
            self.settings[device] = st
            self.clients[device] = build_client(st)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.published = 0

    def _http(self, method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        with self.lock:
            if method == "GET" and path == "/status":
                return 200, {d: {"metric": s.metric, "value": round(s.value, 3), "setpoint": s.setpoint,
                                 "fault": s.fault, "seq": s.seq} for d, s in self.sensors.items()}
            if method == "POST" and path == "/set":
                sensor = self._by_metric(str(body["metric"]))
                value = float(body["value"])
                if not math.isfinite(value):
                    raise ValueError("value must be finite")
                sensor.setpoint = value
                if body.get("jump"):
                    sensor.value = value
                return 200, {"ok": True, "device": sensor.device_id, "setpoint": value}
            if method == "POST" and path == "/profile":
                for s in self.sensors.values():
                    if body.get("metric") in (None, s.metric):
                        s.setpoint = None
                return 200, {"ok": True}
            if method == "POST" and path == "/fault":
                device = str(body["device"])
                if device not in self.sensors:
                    raise ValueError("unknown device")
                fault = body.get("fault")
                if fault is not None and fault not in FAULTS:
                    raise ValueError("unknown fault")
                count = body.get("count")
                sensor = self.sensors[device]
                sensor.fault = fault
                sensor.fault_remaining = int(count) if count else (1 if fault in ONE_SHOT else None)
                return 200, {"ok": True, "device": device, "fault": fault}
        return 404, {"error": "not found"}

    def _by_metric(self, metric: str) -> SensorSim:
        for sensor in self.sensors.values():
            if sensor.metric == metric:
                return sensor
        raise ValueError("unknown metric")

    def run(self) -> None:
        internal_http.serve(self._http)
        for device, client in self.clients.items():
            connect_async(client, self.settings[device])
        interval = self.cfg.platform.timing.telemetry_interval_seconds
        last = time.monotonic()
        while not self.stop.is_set():
            time.sleep(max(0.0, interval - (time.monotonic() - last)))
            now_m = time.monotonic()
            dt, last = now_m - last, now_m
            with self.lock:
                for device, sensor in self.sensors.items():
                    sensor.step(dt)
                    payload = sensor.message()
                    client = self.clients[device]
                    if payload is None or not client.is_connected():
                        continue
                    client.publish(topics.telemetry(self.site_id, device, sensor.metric), payload, qos=0)
                    self.published += 1
            try:
                HEALTH_FILE.write_text(str(time.time()))
            except OSError:
                pass
        for client in self.clients.values():
            client.disconnect()
            client.loop_stop()


def main() -> int:
    site_id = os.environ.get("ORCHID_SITE_ID", "")
    if not topics.SITE_RE.match(site_id):
        return 64
    sim = Simulator(site_id)
    signal.signal(signal.SIGTERM, lambda *_: sim.stop.set())
    signal.signal(signal.SIGINT, lambda *_: sim.stop.set())
    sim.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
