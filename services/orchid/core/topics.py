"""MQTT topic contract (orchid/v1). Builders + strict parser."""
from __future__ import annotations

import re
from dataclasses import dataclass

ROOT = "orchid/v1"
SITE_RE = re.compile(r"^site\d{2}$")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
CENTRAL_AUDIT = f"{ROOT}/central/audit"
GROUP_NAMES = ("fan_group", "dehumidifier_group")
CONTROL_TARGETS = GROUP_NAMES + ("mode",)


def telemetry(site: str, device: str, metric: str) -> str:
    return f"{ROOT}/{site}/sensor/{device}/telemetry/{metric}"


def gateway_telemetry(site: str, device: str, metric: str) -> str:
    """Gateway-processed telemetry (adds gateway_ingest_ts/quality/validation_result)."""
    return f"{ROOT}/{site}/gateway/telemetry/{device}/{metric}"


def actuator_state(site: str, device: str) -> str:
    return f"{ROOT}/{site}/actuator/{device}/state"


def group_command(site: str, group: str) -> str:
    return f"{ROOT}/{site}/control/{group}/command"


def group_ack(site: str, group: str) -> str:
    return f"{ROOT}/{site}/control/{group}/ack"


def mode_command(site: str) -> str:
    return group_command(site, "mode")


def mode_ack(site: str) -> str:
    return group_ack(site, "mode")


def heartbeat(site: str) -> str:
    return f"{ROOT}/{site}/gateway/heartbeat"


def bridge_status(site: str) -> str:
    return f"{ROOT}/{site}/gateway/bridge"


def alerts(site: str) -> str:
    return f"{ROOT}/{site}/alerts"


def events(site: str) -> str:
    return f"{ROOT}/{site}/events"


@dataclass(frozen=True)
class ParsedTopic:
    kind: str
    site: str
    device: str | None = None
    metric: str | None = None
    group: str | None = None


def parse(topic: str) -> ParsedTopic | None:
    """Parse a topic strictly. Unknown shapes return None (deny by default)."""
    parts = topic.split("/")
    if len(parts) < 4 or parts[0] != "orchid" or parts[1] != "v1":
        return None
    site = parts[2]
    if not SITE_RE.match(site):
        return None
    rest = parts[3:]
    if any(not p for p in rest):
        return None
    match rest:
        case ["sensor", dev, "telemetry", metric] if ID_RE.match(dev) and ID_RE.match(metric):
            return ParsedTopic("telemetry", site, device=dev, metric=metric)
        case ["gateway", "telemetry", dev, metric] if ID_RE.match(dev) and ID_RE.match(metric):
            return ParsedTopic("gateway_telemetry", site, device=dev, metric=metric)
        case ["actuator", dev, "state"] if ID_RE.match(dev):
            return ParsedTopic("state", site, device=dev)
        case ["control", group, "command"] if group in CONTROL_TARGETS:
            return ParsedTopic("command", site, group=group)
        case ["control", group, "ack"] if group in CONTROL_TARGETS:
            return ParsedTopic("ack", site, group=group)
        case ["gateway", "heartbeat"]:
            return ParsedTopic("heartbeat", site)
        case ["gateway", "bridge"]:
            return ParsedTopic("bridge", site)
        case ["alerts"]:
            return ParsedTopic("alerts", site)
        case ["events"]:
            return ParsedTopic("events", site)
    return None
