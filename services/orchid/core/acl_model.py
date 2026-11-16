"""Least-privilege MQTT principals and ACLs (single source of truth).

Rendered into Mosquitto Dynamic Security JSON by scripts/bootstrap_secrets.py.
* Default ACL access is DENY for all four access types; every permission is an explicit grant.
* No principal is granted `#`; there is no dynsec admin user (no runtime ACL changes over MQTT).
* Every production principal is bound to a fixed client id, so a stolen credential of one
  principal cannot connect as another principal's client id and take over its session.
* Central-side bridge grants use concrete, registered device/metric/group names (no `+`), so a
  compromised bridge cannot create arbitrary topics/retained messages under its site.
* `probe-*` principals exist only for the automated test probe (test profile); they mirror the
  ACL of the principal they stand in for and can be omitted with bootstrap --no-test-principals.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

R = "orchid/v1"
CONTROL_TARGETS = ("fan_group", "dehumidifier_group", "mode")

DEFAULT_ACL_ACCESS = {
    "publishClientSend": False,
    "publishClientReceive": False,
    "subscribe": False,
    "unsubscribe": False,
}

# Exactly the filters the historian subscribes to (historian/main.py imports this list).
HISTORIAN_FILTERS: tuple[tuple[str, int], ...] = (
    (f"{R}/+/sensor/+/telemetry/+", 1), (f"{R}/+/gateway/telemetry/+/+", 1), (f"{R}/+/actuator/+/state", 1),
    (f"{R}/+/control/+/command", 1), (f"{R}/+/control/+/ack", 1), (f"{R}/+/gateway/heartbeat", 1),
    (f"{R}/+/gateway/bridge", 1), (f"{R}/+/alerts", 1), (f"{R}/+/events", 1), (f"{R}/central/audit", 1),
    ("$SYS/broker/log/#", 0), ("$SYS/broker/uptime", 0), ("$SYS/broker/clients/connected", 0),
    ("$SYS/broker/load/messages/received/1min", 0), ("$SYS/broker/load/messages/sent/1min", 0),
    ("$SYS/broker/load/connections/1min", 0),
)


@dataclass(frozen=True)
class SiteSpec:
    sensors: Mapping[str, str]                 # device -> metric
    actuator_groups: Mapping[str, Sequence[str]]

    def actuators(self) -> list[str]:
        return [m for members in self.actuator_groups.values() for m in members]


@dataclass(frozen=True)
class Principal:
    username: str
    description: str
    publish: tuple[str, ...] = ()
    subscribe: tuple[str, ...] = ()
    clientid: str | None = None

    def acls(self) -> list[dict]:
        out: list[dict] = []
        for topic in self.publish:
            out.append({"acltype": "publishClientSend", "topic": topic, "allow": True})
        for topic in self.subscribe:
            out.append({"acltype": "subscribePattern", "topic": topic, "allow": True})
            out.append({"acltype": "publishClientReceive", "topic": topic, "allow": True})
            out.append({"acltype": "unsubscribePattern", "topic": topic, "allow": True})
        return out


def bridge_outbound_topics(site: str) -> tuple[str, ...]:
    """Site -> Central topic patterns used in the site broker's `topic ... out` bridge lines."""
    return (
        f"{R}/{site}/sensor/+/telemetry/+",
        f"{R}/{site}/gateway/telemetry/+/+",
        f"{R}/{site}/actuator/+/state",
        f"{R}/{site}/control/+/ack",
        f"{R}/{site}/gateway/heartbeat",
        f"{R}/{site}/alerts",
        f"{R}/{site}/events",
    )


def bridge_inbound_topics(site: str) -> tuple[str, ...]:
    """Central -> Site: only authorized control and mode/override commands (concrete topics)."""
    return tuple(f"{R}/{site}/control/{t}/command" for t in CONTROL_TARGETS)


def bridge_central_publish(site: str, spec: SiteSpec) -> tuple[str, ...]:
    """Concrete central-side publish grants for one site's bridge (registered names only)."""
    topics = [f"{R}/{site}/sensor/{d}/telemetry/{m}" for d, m in spec.sensors.items()]
    topics += [f"{R}/{site}/gateway/telemetry/{d}/{m}" for d, m in spec.sensors.items()]
    topics += [f"{R}/{site}/actuator/{a}/state" for a in spec.actuators()]
    topics += [f"{R}/{site}/control/{t}/ack" for t in CONTROL_TARGETS]
    topics += [f"{R}/{site}/gateway/heartbeat", f"{R}/{site}/gateway/bridge", f"{R}/{site}/alerts",
               f"{R}/{site}/events"]
    return tuple(topics)


HEALTH_TOPIC = "$SYS/broker/uptime"


def health_principal() -> Principal:
    return Principal("health", "Container healthcheck (reads broker uptime only)", subscribe=(HEALTH_TOPIC,),
                     clientid="health")


def _central_api(username: str, clientid: str | None) -> Principal:
    return Principal(username, "DMZ API: publishes operator commands and its own audit events",
                     publish=(f"{R}/+/control/+/command", f"{R}/central/audit"), clientid=clientid)


def _historian(username: str, clientid: str | None) -> Principal:
    return Principal(username, "Central historian/audit consumer (read-only)",
                     subscribe=tuple(f for f, _ in HISTORIAN_FILTERS), clientid=clientid)


def _bridge(username: str, site: str, spec: SiteSpec, clientid: str | None) -> Principal:
    return Principal(username, f"Uplink bridge identity for {site}", publish=bridge_central_publish(site, spec),
                     subscribe=bridge_inbound_topics(site), clientid=clientid)


def central_principals(sites: Mapping[str, SiteSpec], *, test_principals: bool = True) -> list[Principal]:
    principals = [health_principal(), _central_api("central-api", "central-api"),
                  _historian("historian", "historian")]
    for site, spec in sites.items():
        principals.append(_bridge(f"bridge-{site}", site, spec, f"bridge-{site}"))
    if test_principals:
        first = next(iter(sites))
        principals += [_central_api("probe-central-api", None), _historian("probe-historian", None),
                       _bridge(f"probe-bridge-{first}", first, sites[first], None)]
    return principals


def _controller(username: str, site: str, clientid: str | None) -> Principal:
    return Principal(
        username, f"Site controller for {site}",
        publish=(f"{R}/{site}/gateway/telemetry/+/+", f"{R}/{site}/actuator/+/state", f"{R}/{site}/control/+/ack",
                 f"{R}/{site}/gateway/heartbeat", f"{R}/{site}/alerts", f"{R}/{site}/events"),
        subscribe=(f"{R}/{site}/sensor/+/telemetry/+", f"{R}/{site}/control/+/command"), clientid=clientid)


def _sensor(username: str, site: str, device: str, metric: str, clientid: str | None) -> Principal:
    return Principal(username, f"Field sensor {device} ({metric}) at {site}",
                     publish=(f"{R}/{site}/sensor/{device}/telemetry/{metric}",), clientid=clientid)


def site_principals(site: str, sensors: Mapping[str, str], *, test_principals: bool = False) -> list[Principal]:
    principals = [health_principal(), _controller(f"{site}-controller", site, f"{site}-controller")]
    for device, metric in sensors.items():
        principals.append(_sensor(f"{site}-{device}", site, device, metric, f"{site}-{device}"))
    if test_principals:
        principals.append(_controller(f"probe-{site}-controller", site, None))
        for device, metric in sensors.items():
            principals.append(_sensor(f"probe-{site}-{device}", site, device, metric, None))
    return principals


def dynsec_document(principals: list[Principal], encoded_passwords: dict[str, str]) -> dict:
    clients, roles = [], []
    for p in principals:
        role = f"role-{p.username}"
        client = {
            "username": p.username,
            "textName": p.description,
            "encoded_password": encoded_passwords[p.username],
            "roles": [{"rolename": role}],
        }
        if p.clientid:
            client["clientid"] = p.clientid
        clients.append(client)
        roles.append({"rolename": role, "textName": p.description, "acls": p.acls()})
    return {
        "defaultACLAccess": dict(DEFAULT_ACL_ACCESS),
        "clients": clients,
        "groups": [],
        "roles": roles,
    }
