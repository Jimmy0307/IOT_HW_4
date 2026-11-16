"""Read-only views over the historian DB for the dashboard API."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from orchid.central_api import status
from orchid.core.config import GROUPS, Config
from orchid.core.timeutil import iso
from orchid.historian.db import HistorianDB

TREND_MINUTES = 10
TREND_POINTS = 300
ALERT_TYPES_ACTIVE = ("ACTUATOR_PARTIAL_FAILURE", "ACTUATOR_FAILURE", "SENSOR_STALE", "SENSOR_FROZEN",
                      "FAILSAFE", "INTERLOCK_ACTIVE", "LOCAL_LOG_DEGRADED")


class DataUnavailable(RuntimeError):
    pass


class Store:
    def __init__(self, cfg: Config, db_path: Path) -> None:
        self.cfg = cfg
        self.db_path = db_path
        self._db: HistorianDB | None = None
        t = cfg.platform.timing
        self.hb_stale, self.state_stale, self.tel_stale = (
            t.heartbeat_stale_seconds, t.state_stale_seconds, t.telemetry_stale_seconds)

    def _conn(self) -> HistorianDB:
        if self._db is None:
            if not self.db_path.exists():
                raise DataUnavailable("historian database not available yet")
            self._db = HistorianDB(self.db_path, readonly=True)
        return self._db

    def q(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        try:
            return self._conn().query(sql, args)
        except DataUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as 503, never crash the API
            self._db = None
            raise DataUnavailable(type(exc).__name__) from exc

    # ------------------------------------------------------------------ site views
    def _latest_reading(self, site: str, metric: str) -> dict[str, Any] | None:
        rows = self.q("SELECT * FROM validated_telemetry WHERE site=? AND metric=? AND validation_result='VALIDATED' "
                      "ORDER BY id DESC LIMIT 1", (site, metric))
        return rows[0] if rows else None

    def _latest_any(self, site: str, metric: str) -> dict[str, Any] | None:
        rows = self.q("SELECT quality, validation_result, gateway_ingest_ts FROM validated_telemetry "
                      "WHERE site=? AND metric=? ORDER BY id DESC LIMIT 1", (site, metric))
        return rows[0] if rows else None

    def site_summary(self, site_id: str, now: datetime) -> dict[str, Any]:
        site = self.cfg.sites[site_id]
        st_rows = self.q("SELECT * FROM site_status WHERE site=?", (site_id,))
        st_row = st_rows[0] if st_rows else None
        site_state = status.site_status(st_row, now=now, stale_s=self.hb_stale)
        hb = json.loads(st_row["heartbeat"]) if st_row and st_row.get("heartbeat") else {}
        states = {r["device_id"]: r for r in self.q("SELECT * FROM actuator_state WHERE site=?", (site_id,))}
        actuators, groups = {}, {}
        for group in GROUPS:
            views = []
            for member in site.actuator_groups[group]:
                view = status.actuator_view(states.get(member), site_status=site_state, now=now,
                                            stale_s=self.state_stale)
                actuators[member] = {**view, "group": group}
                views.append(view)
            groups[group] = {"members": site.actuator_groups[group], "display": status.group_display(views)}
        readings = {}
        for metric in ("temperature", "humidity"):
            high, low = site.thresholds(metric)
            crit = site.interlock.temperature_high if metric == "temperature" else None
            readings[metric] = status.reading_view(self._latest_reading(site_id, metric), now=now,
                                                   stale_s=self.tel_stale, high=high, low=low, critical_high=crit)
            last_any = self._latest_any(site_id, metric)
            readings[metric]["last_result"] = last_any["validation_result"] if last_any else None
        qualities = [r for r in readings.values()]
        if all(r["display"] == "UNKNOWN" for r in qualities):
            data_quality = "UNKNOWN"
        elif any(not r["fresh"] for r in qualities):
            data_quality = "STALE"
        elif any(r.get("last_result") not in (None, "VALIDATED") for r in qualities):
            data_quality = "SUSPECT"
        else:
            data_quality = "GOOD"
        issues = [i for i in hb.get("issues", []) if isinstance(i, str)][:10] if site_state != "UNKNOWN" else []
        last_updated = max(filter(None, [st_row.get("hb_ingest_ts") if st_row else None,
                                         *(r.get("gateway_ingest_ts") for r in qualities)]), default=None)
        return {
            "site_id": site_id, "display_name": site.display_name, "status": site_state,
            "mode": hb.get("mode") if site_state not in ("UNKNOWN",) else None,
            "health": hb.get("health"), "issues": issues,
            "bridge": {"1": "UP", "0": "DOWN"}.get(st_row.get("bridge_state") if st_row else None, "UNKNOWN"),
            "temperature": readings["temperature"], "humidity": readings["humidity"],
            "actuators": actuators, "groups": groups, "data_quality": data_quality,
            "last_updated": last_updated, "active_alerts": len([i for i in issues if i in ALERT_TYPES_ACTIVE]),
            "targets": {"temperature": site.target_temperature, "humidity": site.target_humidity,
                        "temperature_band": list(site.thresholds("temperature")),
                        "humidity_band": list(site.thresholds("humidity")),
                        "interlock_temperature": site.interlock.temperature_high},
        }

    def overview(self, now: datetime) -> dict[str, Any]:
        return {"generated_at": iso(now), "sites": [self.site_summary(s, now) for s in sorted(self.cfg.sites)]}

    def site_detail(self, site_id: str, now: datetime) -> dict[str, Any]:
        summary = self.site_summary(site_id, now)
        since = iso(now - timedelta(minutes=TREND_MINUTES))
        trend = {}
        for metric in ("temperature", "humidity"):
            rows = self.q("SELECT gateway_ingest_ts AS t, value AS v, quality AS q FROM validated_telemetry "
                          "WHERE site=? AND metric=? AND validation_result='VALIDATED' AND gateway_ingest_ts >= ? "
                          "ORDER BY id DESC LIMIT ?", (site_id, metric, since, TREND_POINTS))
            trend[metric] = list(reversed(rows))
        quality = {}
        for metric in ("temperature", "humidity"):
            rows = self.q("SELECT validation_result AS r, COUNT(*) AS n FROM (SELECT validation_result FROM "
                          "validated_telemetry WHERE site=? AND metric=? ORDER BY id DESC LIMIT 200) GROUP BY r",
                          (site_id, metric))
            quality[metric] = {r["r"]: r["n"] for r in rows}
        commands = self.q("SELECT command_id, grp AS \"group\", requested, operator, reason, status, detail, members, "
                          "source, mode, acked_at, created_ts FROM commands WHERE site=? "
                          "ORDER BY updated_ts DESC LIMIT 20", (site_id,))
        for c in commands:
            c["members"] = json.loads(c["members"]) if c.get("members") else {}
        alerts = self.events(site=site_id, kinds=("alert", "security"), limit=20)
        st = self.q("SELECT heartbeat, hb_ingest_ts, bridge_state, bridge_ts FROM site_status WHERE site=?", (site_id,))
        gateway = {}
        if st:
            gateway = {"heartbeat": json.loads(st[0]["heartbeat"]) if st[0]["heartbeat"] else None,
                       "heartbeat_age_s": status.age_seconds(st[0]["hb_ingest_ts"], now),
                       "bridge_state": st[0]["bridge_state"], "bridge_ts": st[0]["bridge_ts"]}
        return {**summary, "trend": trend, "sensor_quality": quality, "recent_commands": commands,
                "recent_alerts": alerts, "gateway": gateway}

    # ------------------------------------------------------------------ events / system
    def events(self, *, site: str | None = None, kinds: tuple[str, ...] | None = None, severity: str | None = None,
               limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT id, ingest_ts, site, principal, kind, event_type, severity, event_id, claimed_ts, details " \
              "FROM events WHERE 1=1"
        args: list[Any] = []
        if site:
            sql += " AND site=?"
            args.append(site)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args.extend(kinds)
        if severity:
            sql += " AND severity=?"
            args.append(severity)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(limit, 200)))
        rows = self.q(sql, tuple(args))
        for r in rows:
            r["details"] = json.loads(r["details"])
        return rows

    def system(self, now: datetime) -> dict[str, Any]:
        metrics = {r["key"]: r for r in self.q("SELECT * FROM metrics")}

        def m(key: str, default: Any = 0) -> Any:
            row = metrics.get(key)
            if not row:
                return default
            try:
                return float(row["value"]) if "." in row["value"] else int(row["value"])
            except ValueError:
                return row["value"]

        sites = [self.site_summary(s, now) for s in sorted(self.cfg.sites)]
        counts = {r["event_type"]: r["n"] for r in self.q(
            "SELECT event_type, COUNT(*) AS n FROM events GROUP BY event_type")}
        cmd_counts = {r["status"]: r["n"] for r in self.q("SELECT status, COUNT(*) AS n FROM commands GROUP BY status")}
        broker_row = metrics.get("broker_uptime_s") or metrics.get("broker_clients_connected")
        broker_age = status.age_seconds(broker_row["updated_ts"], now) if broker_row else None
        hist_age = status.age_seconds(metrics["historian_heartbeat"]["value"], now) \
            if "historian_heartbeat" in metrics else None
        audit_row = metrics.get("audit_integrity")
        return {
            "generated_at": iso(now),
            "broker": {"status": "UP" if broker_age is not None and broker_age <= 30 else
                       ("STALE" if broker_age is not None else "UNKNOWN"),
                       "clients_connected": m("broker_clients_connected", None),
                       "msgs_received_1min": m("broker_msgs_received_1min", None),
                       "msgs_sent_1min": m("broker_msgs_sent_1min", None),
                       "uptime_s": m("broker_uptime_s", None), "age_s": broker_age},
            "historian": {"connected": bool(m("historian_mqtt_connected", 0)), "heartbeat_age_s": hist_age},
            "sites_total": len(sites),
            "sites_connected": sum(1 for s in sites if s["status"] in ("ONLINE", "DEGRADED")),
            "site_status": {s["site_id"]: s["status"] for s in sites},
            "stale_sensors": sum(1 for s in sites for k in ("temperature", "humidity") if not s[k]["fresh"]),
            "security": {
                "broker_auth_failures": counts.get("BROKER_AUTH_FAILURE", 0),
                "broker_acl_denials_logged": counts.get("BROKER_ACL_DENIED", 0),
                "login_failures": counts.get("LOGIN_FAILURE", 0),
                "authz_denied": counts.get("AUTHZ_DENIED", 0),
                "event_schema_rejected": counts.get("EVENT_SCHEMA_REJECTED", 0),
                "untrusted_claims_dropped": counts.get("UNTRUSTED_CLAIM_DROPPED", 0),
                "topic_identity_mismatch": counts.get("TOPIC_IDENTITY_MISMATCH", 0),
                "replay_detected": counts.get("REPLAY_DETECTED", 0),
                "rate_limited_events": m("ingest_rate_limited", 0),
                "command_rejected": counts.get("COMMAND_REJECTED", 0),
                "command_invalid": counts.get("COMMAND_INVALID", 0),
            },
            "commands": {k.lower(): v for k, v in cmd_counts.items()},
            "message_rate": {"broker_received_1min": m("broker_msgs_received_1min", None),
                             "late_telemetry": m("ingest_late_telemetry", 0),
                             "gateway_telemetry": m("ingest_gateway_telemetry", 0)},
            "audit": {"integrity": audit_row["value"] if audit_row else "UNKNOWN",
                      "rows": m("audit_rows", 0), "checked_at": audit_row["updated_ts"] if audit_row else None,
                      "head_prefix": m("audit_head", None)},
            "ingest": {"queue_dropped_critical": m("queue_dropped_critical", 0),
                       "queue_dropped_normal": m("queue_dropped_normal", 0),
                       "errors": m("ingest_ingest_errors", 0)},
        }
