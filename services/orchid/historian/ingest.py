"""Central ingest pipeline. Every inbound message is untrusted.

Log/event path:
  untrusted event -> size/depth limit -> schema + event_type allowlist
  -> CR/LF/control-char neutralization -> authenticated principal binding (topic ACL)
  -> site from topic -> server ingest_ts -> server severity policy
  -> event_id dedup -> rate limiting (per site, per class) -> events + HMAC audit chain

Authoritative values (principal, site, ingest_ts, severity) are always derived
server-side; client claims are dropped and the drop itself is recorded.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from collections import OrderedDict, defaultdict
from datetime import datetime
from typing import Any, Callable

from orchid.core import topics
from orchid.core.audit_chain import GENESIS, VerifyResult, chain_append, verify_chain
from orchid.core.config import Config
from orchid.core.jsonsafe import PayloadError, parse_json_bounded
from orchid.core.sanitize import EventRejected, neutralize_text, sanitize_event
from orchid.core.timeutil import iso, parse_aware, utcnow
from orchid.historian.db import HistorianDB
from orchid.historian.policy import ALLOWED_EVENT_TYPES, CENTRAL_API_EVENT_TYPES, is_critical, severity_for
from orchid.historian.ratelimit import TokenBucket

AUTH_FAIL_RE = re.compile(r"not authori[sz]ed|bad user name or password", re.IGNORECASE)
ACL_DENY_RE = re.compile(r"denied (publish|subscribe)", re.IGNORECASE)
SYS_METRICS = {
    "$SYS/broker/uptime": "broker_uptime_s",  # republished every sys_interval -> liveness signal
    "$SYS/broker/clients/connected": "broker_clients_connected",
    "$SYS/broker/load/messages/received/1min": "broker_msgs_received_1min",
    "$SYS/broker/load/messages/sent/1min": "broker_msgs_sent_1min",
    "$SYS/broker/load/connections/1min": "broker_connections_1min",
}
SUMMARY_INTERVAL_S = 10.0
DEDUP_LRU = 20000
SITE_TEXT_MAX = 200


def _principal_for_site(site: str) -> str:
    """Only bridge-<site> may publish under orchid/v1/<site>/ on the central broker (dynsec ACL)."""
    return f"bridge-{site}"


class Ingestor:
    def __init__(self, cfg: Config, db: HistorianDB, hmac_key: bytes, *,
                 now: Callable[[], datetime] = utcnow, monotonic: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.db = db
        self.key = hmac_key
        self.now = now
        self.monotonic = monotonic
        self.limits = cfg.platform.payload_limits
        self.audit_cfg = cfg.platform.audit
        h = cfg.platform.historian
        self.retention_limits = {"raw_telemetry": h.raw_retention_rows,
                                 "validated_telemetry": h.gateway_retention_rows,
                                 "events": h.events_retention_rows}
        self.counters: dict[str, int] = defaultdict(int)
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self._dropped: dict[str, int] = defaultdict(int)
        self._last_summary = monotonic()
        self._dedup: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._prev = db.last_hmac() or GENESIS

    # ------------------------------------------------------------------ routing
    def handle(self, topic: str, raw: bytes) -> None:
        try:
            self._route(topic, raw)
        except Exception as exc:  # noqa: BLE001 - one bad message must never stop ingest
            self.counters["ingest_errors"] += 1
            self.counters[f"ingest_error_{type(exc).__name__}"] += 1

    def _route(self, topic: str, raw: bytes) -> None:
        if topic.startswith("$SYS/"):
            self._sys(topic, raw)
            return
        if topic == topics.CENTRAL_AUDIT:
            self._central_audit(raw)
            return
        parsed = topics.parse(topic)
        if parsed is None or parsed.site not in self.cfg.sites:
            self.counters["unknown_topic"] += 1
            return
        handler = {
            "telemetry": self._raw_telemetry, "gateway_telemetry": self._gateway_telemetry,
            "state": self._state, "command": self._command, "ack": self._ack, "heartbeat": self._heartbeat,
            "bridge": self._bridge, "alerts": self._site_event, "events": self._site_event,
        }[parsed.kind]
        handler(parsed, raw)

    def _parse(self, raw: bytes, max_bytes: int) -> Any:
        return parse_json_bounded(raw, max_bytes=max_bytes, max_depth=self.limits.max_json_depth)

    # ------------------------------------------------------------------ events / audit
    def _bucket(self, site: str, cls: str) -> TokenBucket:
        key = (site, cls)
        if key not in self._buckets:
            rl = self.audit_cfg.rate_limit
            rate, burst = ((rl.critical_per_second, rl.critical_burst) if cls != "normal"
                           else (rl.normal_per_second, rl.normal_burst))
            self._buckets[key] = TokenBucket(rate, burst, clock=self.monotonic)
        return self._buckets[key]

    def record(self, *, site: str, principal: str, kind: str, event_type: str, details: dict[str, Any],
               event_id: str | None = None, claimed_ts: str | None = None, rate_class: str | None = None) -> bool:
        severity = severity_for(event_type)
        cls = rate_class or ("critical" if is_critical(severity) else "normal")
        if cls != "exempt" and not self._bucket(site, cls).allow():
            self._dropped[site] += 1
            self.counters["rate_limited"] += 1
            return False
        event_id = event_id or f"srv-{uuid.uuid4()}"
        dkey = (site, event_id)
        if dkey in self._dedup:
            self.counters["duplicates"] += 1
            return False
        ingest_ts = iso(self.now())
        details_json = json.dumps(details, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        with self.db.transaction():
            cur = self.db.execute(
                "INSERT OR IGNORE INTO events(ingest_ts, site, principal, kind, event_type, severity, event_id, "
                "claimed_ts, details) VALUES(?,?,?,?,?,?,?,?,?)",
                (ingest_ts, site, principal, kind, event_type, severity, event_id, claimed_ts, details_json))
            if cur.rowcount == 0:
                self.counters["duplicates"] += 1
                self._remember(dkey)
                return False
            row = chain_append(self.key, self._prev, {
                "event_id": event_id, "ingest_ts": ingest_ts, "principal": principal, "site": site,
                "event_type": event_type, "severity": severity, "details": json.loads(details_json)})
            self.db.execute(
                "INSERT INTO audit_log(event_id, ingest_ts, principal, site, event_type, severity, details, "
                "prev_hmac, hmac) VALUES(?,?,?,?,?,?,?,?,?)",
                (event_id, ingest_ts, principal, site, event_type, severity, details_json,
                 row["prev_hmac"], row["hmac"]))
        self._prev = row["hmac"]
        self._remember(dkey)
        self.counters[f"severity_{severity}"] += 1
        return True

    def _remember(self, key: tuple[str, str]) -> None:
        self._dedup[key] = None
        while len(self._dedup) > DEDUP_LRU:
            self._dedup.popitem(last=False)

    def _security(self, site: str, principal: str, event_type: str, **details: Any) -> None:
        self.counters[f"security_{event_type}"] += 1
        self.record(site=site, principal=principal, kind="security", event_type=event_type,
                    details=details, rate_class="server")

    def _site_event(self, parsed: topics.ParsedTopic, raw: bytes) -> None:
        site = parsed.site
        principal = _principal_for_site(site)
        kind = "alert" if parsed.kind == "alerts" else "event"
        try:
            obj = self._parse(raw, self.limits.max_event_bytes)
            clean = sanitize_event(obj, allowed_types=ALLOWED_EVENT_TYPES,
                                   max_detail_keys=self.audit_cfg.max_detail_keys,
                                   max_len=self.audit_cfg.max_detail_string_len)
        except PayloadError as exc:
            self._security(site, principal, "EVENT_SCHEMA_REJECTED", code=exc.code, topic_kind=kind)
            return
        except EventRejected as exc:
            self._security(site, principal, "EVENT_SCHEMA_REJECTED", code=exc.code,
                           detail=neutralize_text(exc.detail, 64), topic_kind=kind)
            return
        if clean["untrusted_claims_dropped"]:
            self._security(site, principal, "UNTRUSTED_CLAIM_DROPPED", event_id=clean["event_id"],
                           fields=",".join(clean["untrusted_claims_dropped"]))
        details = dict(clean["details"])
        if clean["details_truncated"]:
            details["_details_truncated"] = True
        if clean["unknown_fields_dropped"]:
            details["_unknown_fields_dropped"] = ",".join(clean["unknown_fields_dropped"])
        self.record(site=site, principal=principal, kind=kind, event_type=clean["event_type"], details=details,
                    event_id=clean["event_id"], claimed_ts=clean["claimed_ts"])

    def _central_audit(self, raw: bytes) -> None:
        try:
            obj = self._parse(raw, self.limits.max_event_bytes)
            clean = sanitize_event(obj, allowed_types=CENTRAL_API_EVENT_TYPES,
                                   max_detail_keys=self.audit_cfg.max_detail_keys,
                                   max_len=self.audit_cfg.max_detail_string_len)
        except (PayloadError, EventRejected) as exc:
            self._security("central", "central-api", "EVENT_SCHEMA_REJECTED", code=getattr(exc, "code", "?"))
            return
        site = clean["details"].get("site")
        site = site if isinstance(site, str) and site in self.cfg.sites else "central"
        self.record(site=site, principal="central-api", kind="audit", event_type=clean["event_type"],
                    details=clean["details"], event_id=clean["event_id"], claimed_ts=clean["claimed_ts"],
                    rate_class="critical")

    def _sys(self, topic: str, raw: bytes) -> None:
        text = raw[:1024].decode("utf-8", errors="replace")
        if topic.startswith("$SYS/broker/log/"):
            if AUTH_FAIL_RE.search(text):
                self.counters["auth_failures"] += 1
                self._security("central", "central-broker", "BROKER_AUTH_FAILURE",
                               message=neutralize_text(text, SITE_TEXT_MAX))
            elif ACL_DENY_RE.search(text):
                self.counters["acl_denials"] += 1
                self._security("central", "central-broker", "BROKER_ACL_DENIED",
                               message=neutralize_text(text, SITE_TEXT_MAX))
            return
        key = SYS_METRICS.get(topic)
        if key:
            try:
                value = float(text.strip().split()[0])
            except ValueError:
                return
            self.db.set_metric(key, value, iso(self.now()))

    # ------------------------------------------------------------------ telemetry / state
    def _identity_ok(self, parsed: topics.ParsedTopic, obj: Any, device_field: str = "device_id") -> bool:
        ok = isinstance(obj, dict) and obj.get("site_id", parsed.site) == parsed.site and (
            parsed.device is None or obj.get(device_field) == parsed.device)
        if ok and parsed.metric is not None:
            ok = obj.get("metric") == parsed.metric
        if not ok:
            self._security(parsed.site, _principal_for_site(parsed.site), "TOPIC_IDENTITY_MISMATCH",
                           topic_kind=parsed.kind, device=parsed.device or "")
        return ok

    def _raw_telemetry(self, parsed: topics.ParsedTopic, raw: bytes) -> None:
        text = raw[: self.limits.max_telemetry_bytes].decode("utf-8", errors="replace")
        value = device_ts = seq = None
        parse_ok = 0
        try:
            obj = self._parse(raw, self.limits.max_telemetry_bytes)
            if isinstance(obj, dict):
                parse_ok = 1
                v = obj.get("value")
                value = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
                device_ts = obj.get("device_ts") if isinstance(obj.get("device_ts"), str) else None
                seq = obj.get("seq") if isinstance(obj.get("seq"), int) else None
        except PayloadError:
            pass
        self.db.execute("INSERT INTO raw_telemetry(central_ingest_ts, site, device_id, metric, payload, parse_ok, "
                        "value, device_ts, seq) VALUES(?,?,?,?,?,?,?,?,?)",
                        (iso(self.now()), parsed.site, parsed.device, parsed.metric, text, parse_ok, value,
                         device_ts, seq))
        self.counters["raw_telemetry"] += 1

    def _gateway_telemetry(self, parsed: topics.ParsedTopic, raw: bytes) -> None:
        try:
            obj = self._parse(raw, self.limits.max_telemetry_bytes)
        except PayloadError:
            self.counters["gateway_rejected"] += 1
            return
        if not self._identity_ok(parsed, obj):
            return
        now = self.now()
        late = 0
        try:
            gw_ts = parse_aware(str(obj.get("gateway_ingest_ts")))
            late = int((now - gw_ts).total_seconds() > self.cfg.platform.historian.late_threshold_seconds)
        except ValueError:
            gw_ts = None
        value = obj.get("value")
        value = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        self.db.execute(
            "INSERT INTO validated_telemetry(central_ingest_ts, gateway_ingest_ts, site, device_id, metric, value, "
            "unit, device_ts, seq, quality, validation_result, control_eligible, late) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iso(now), iso(gw_ts) if gw_ts else None, parsed.site, parsed.device, parsed.metric, value,
             neutralize_text(str(obj.get("unit", "")), 8), neutralize_text(str(obj.get("device_ts", "")), 40),
             obj.get("seq") if isinstance(obj.get("seq"), int) else None,
             neutralize_text(str(obj.get("quality", "BAD")), 16),
             neutralize_text(str(obj.get("validation_result", "UNKNOWN")), 32),
             int(obj.get("control_eligible") is True), late))
        self.counters["late_telemetry" if late else "gateway_telemetry"] += 1

    def _state(self, parsed: topics.ParsedTopic, raw: bytes) -> None:
        try:
            obj = self._parse(raw, 1024)
        except PayloadError:
            return
        if not self._identity_ok(parsed, obj):
            return
        t = lambda k, n=32: neutralize_text(str(obj.get(k, "")), n)  # noqa: E731
        self.db.execute(
            "INSERT INTO actuator_state(site, device_id, grp, state, commanded, quality, observed_at, source, "
            "central_ingest_ts) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(site, device_id) DO UPDATE SET "
            "grp=excluded.grp, state=excluded.state, commanded=excluded.commanded, quality=excluded.quality, "
            "observed_at=excluded.observed_at, source=excluded.source, central_ingest_ts=excluded.central_ingest_ts",
            (parsed.site, parsed.device, t("group"), t("state", 8), t("commanded", 8), t("quality", 8),
             t("observed_at", 40), t("source", 48), iso(self.now())))

    def _heartbeat(self, parsed: topics.ParsedTopic, raw: bytes) -> None:
        try:
            obj = self._parse(raw, 2048)
        except PayloadError:
            return
        if not self._identity_ok(parsed, obj):
            return
        status = neutralize_text(str(obj.get("status", "UNKNOWN")), 16)
        self.db.execute(
            "INSERT INTO site_status(site, heartbeat, hb_status, hb_ts, hb_ingest_ts) VALUES(?,?,?,?,?) "
            "ON CONFLICT(site) DO UPDATE SET heartbeat=excluded.heartbeat, hb_status=excluded.hb_status, "
            "hb_ts=excluded.hb_ts, hb_ingest_ts=excluded.hb_ingest_ts",
            (parsed.site, json.dumps(obj, ensure_ascii=True)[:2048], status,
             neutralize_text(str(obj.get("ts", "")), 40), iso(self.now())))

    def _bridge(self, parsed: topics.ParsedTopic, raw: bytes) -> None:
        state = raw[:8].decode("ascii", errors="replace").strip()
        if state not in ("0", "1"):
            return
        self.db.execute("INSERT INTO site_status(site, bridge_state, bridge_ts) VALUES(?,?,?) ON CONFLICT(site) "
                        "DO UPDATE SET bridge_state=excluded.bridge_state, bridge_ts=excluded.bridge_ts",
                        (parsed.site, state, iso(self.now())))

    # ------------------------------------------------------------------ commands
    def _command(self, parsed: topics.ParsedTopic, raw: bytes) -> None:
        try:
            obj = self._parse(raw, self.limits.max_command_bytes)
        except PayloadError:
            return
        if not isinstance(obj, dict) or not isinstance(obj.get("command_id"), str):
            return
        t = lambda k, n=64: neutralize_text(str(obj.get(k, "")), n)  # noqa: E731
        requested = t("desired_state", 8) if parsed.group != "mode" else t("mode", 24)
        now = iso(self.now())
        self.db.execute(
            "INSERT INTO commands(command_id, site, grp, requested, operator, reason, issued_at, expires_at, status, "
            "created_ts, updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(command_id) DO NOTHING",
            (t("command_id", 40), parsed.site, parsed.group, requested, t("operator"), t("reason"),
             t("issued_at", 40), t("expires_at", 40), "SENT", now, now))

    def _ack(self, parsed: topics.ParsedTopic, raw: bytes) -> None:
        try:
            obj = self._parse(raw, self.limits.max_command_bytes)
        except PayloadError:
            return
        if not isinstance(obj, dict) or obj.get("site_id", parsed.site) != parsed.site:
            return
        t = lambda k, n=64: neutralize_text(str(obj.get(k) or ""), n)  # noqa: E731
        cid = t("command_id", 40) or f"unknown-{uuid.uuid4()}"
        result = t("result", 24)
        members = obj.get("members") if isinstance(obj.get("members"), dict) else {}
        members_json = json.dumps({neutralize_text(str(k), 24): neutralize_text(str(v), 8)
                                   for k, v in list(members.items())[:4]})
        requested = t("requested_state", 8) if parsed.group != "mode" else t("requested_mode", 24)
        now = iso(self.now())
        if result == "DUPLICATE":
            self.db.execute("UPDATE commands SET detail=?, updated_ts=? WHERE command_id=?",
                            ("duplicate delivery ignored (idempotent)", now, cid))
            self.counters["acks_duplicate"] += 1
            return
        self.db.execute(
            "INSERT INTO commands(command_id, site, grp, requested, operator, reason, status, detail, members, source, "
            "mode, acked_at, created_ts, updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(command_id) DO UPDATE SET status=excluded.status, detail=excluded.detail, "
            "members=excluded.members, source=excluded.source, mode=excluded.mode, acked_at=excluded.acked_at, "
            "updated_ts=excluded.updated_ts",
            (cid, parsed.site, parsed.group, requested, t("operator"), t("reason"), result, t("detail", 120),
             members_json, t("source", 32), t("mode", 24), t("acked_at", 40), now, now))
        self.counters[f"acks_{result.lower()}"] += 1

    # ------------------------------------------------------------------ housekeeping
    def periodic(self) -> None:
        if self.monotonic() - self._last_summary >= SUMMARY_INTERVAL_S:
            self._last_summary = self.monotonic()
            for site, dropped in list(self._dropped.items()):
                if dropped:
                    self.record(site=site, principal="historian", kind="security", event_type="RATE_LIMITED",
                                details={"dropped": dropped, "window_s": SUMMARY_INTERVAL_S},
                                rate_class="exempt")
            self._dropped.clear()
        ts = iso(self.now())
        for key, value in self.counters.items():
            self.db.set_metric(f"ingest_{key}", value, ts)

    def prune(self) -> None:
        for table, keep in self.retention_limits.items():
            row = self.db.query(f"SELECT MAX(id) AS m FROM {table}")  # noqa: S608 - fixed table names
            top = row[0]["m"] if row else None
            if top is not None and top > keep:
                self.db.execute(f"DELETE FROM {table} WHERE id <= ?", (top - keep,))  # noqa: S608

    def verify_audit(self) -> VerifyResult:
        return self.verify_db(self.db, self.key)

    @staticmethod
    def verify_db(db: HistorianDB, key: bytes) -> VerifyResult:
        def rows():
            for r in db.audit_rows():
                r = dict(r)
                r["details"] = json.loads(r["details"])
                yield r
        return verify_chain(key, rows())
