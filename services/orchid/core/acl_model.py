"""Least-privilege MQTT principals and ACLs (single source of truth).

Rendered into Mosquitto Dynamic Security JSON by scripts/bootstrap_secrets.py.
Default ACL access is DENY for all four access types; every permission below is
an explicit grant. No principal is granted `#`, and no dynsec admin user exists
(ACLs cannot be changed at runtime over MQTT).
"""
from __future__ import annotations

from dataclasses import dataclass, field

R = "orchid/v1"

DEFAULT_ACL_ACCESS = {
    "publishClientSend": False,
    "publishClientReceive": False,
    "subscribe": False,
    "unsubscribe": False,
}


@dataclass(frozen=True)
class Principal:
    username: str
    description: str
    publish: tuple[str, ...] = ()
    subscribe: tuple[str, ...] = ()
    clientid_prefix: str | None = None
    extra_acls: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def acls(self) -> list[dict]:
        out: list[dict] = []
        for topic in self.publish:
            out.append({"acltype": "publishClientSend", "topic": topic, "allow": True})
        for topic in self.subscribe:
            out.append({"acltype": "subscribePattern", "topic": topic, "allow": True})
            out.append({"acltype": "publishClientReceive", "topic": topic, "allow": True})
            out.append({"acltype": "unsubscribePattern", "topic": topic, "allow": True})
        for acltype, topic in self.extra_acls:
            out.append({"acltype": acltype, "topic": topic, "allow": True})
        return out


def bridge_outbound_topics(site: str) -> tuple[str, ...]:
    """Site -> Central allowlist (also used to render the bridge `topic ... out` lines)."""
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
    """Central -> Site allowlist: authorized control and mode/override commands only."""
    return (f"{R}/{site}/control/+/command",)


HEALTH_TOPIC = "$SYS/broker/uptime"


def health_principal() -> Principal:
    return Principal("health", "Container healthcheck (reads broker uptime only)", subscribe=(HEALTH_TOPIC,))


def central_principals(sites: list[str]) -> list[Principal]:
    principals = [
        health_principal(),
        Principal(
            "central-api",
            "DMZ API: publishes operator commands and its own audit events",
            publish=(f"{R}/+/control/+/command", f"{R}/central/audit"),
        ),
        Principal(
            "historian",
            "Central historian/audit consumer (read-only)",
            subscribe=(
                f"{R}/#",
                "$SYS/broker/log/#",
                "$SYS/broker/uptime",
                "$SYS/broker/clients/connected",
                "$SYS/broker/load/messages/received/1min",
                "$SYS/broker/load/messages/sent/1min",
                "$SYS/broker/load/connections/1min",
            ),
        ),
    ]
    for site in sites:
        principals.append(
            Principal(
                f"bridge-{site}",
                f"Uplink bridge identity for {site}",
                publish=bridge_outbound_topics(site) + (f"{R}/{site}/gateway/bridge",),
                subscribe=bridge_inbound_topics(site),
            )
        )
    return principals


def site_principals(site: str, sensors: dict[str, str]) -> list[Principal]:
    principals = [
        health_principal(),
        Principal(
            f"{site}-controller",
            f"Site controller for {site}",
            publish=(
                f"{R}/{site}/gateway/telemetry/+/+",
                f"{R}/{site}/actuator/+/state",
                f"{R}/{site}/control/+/ack",
                f"{R}/{site}/gateway/heartbeat",
                f"{R}/{site}/alerts",
                f"{R}/{site}/events",
            ),
            subscribe=(f"{R}/{site}/sensor/+/telemetry/+", f"{R}/{site}/control/+/command"),
        ),
    ]
    for device, metric in sensors.items():
        principals.append(
            Principal(
                f"{site}-{device}",
                f"Field sensor {device} ({metric}) at {site}",
                publish=(f"{R}/{site}/sensor/{device}/telemetry/{metric}", f"{R}/{site}/events"),
            )
        )
    return principals


def dynsec_document(principals: list[Principal], encoded_passwords: dict[str, str]) -> dict:
    clients, roles = [], []
    for p in principals:
        role = f"role-{p.username}"
        clients.append({
            "username": p.username,
            "textName": p.description,
            "encoded_password": encoded_passwords[p.username],
            "roles": [{"rolename": role}],
        })
        roles.append({"rolename": role, "textName": p.description, "acls": p.acls()})
    return {
        "defaultACLAccess": dict(DEFAULT_ACL_ACCESS),
        "clients": clients,
        "groups": [],
        "roles": roles,
    }
