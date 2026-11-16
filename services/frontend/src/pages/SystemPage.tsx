import { api } from "../api/client";
import type { EventsResponse, SystemResponse } from "../api/types";
import { Badge, StaleContext } from "../components/Badge";
import { DataBanner } from "../components/DataBanner";
import { EventsTable } from "../components/EventsTable";
import { usePolling } from "../hooks/usePolling";
import { noteServerTime } from "../lib/clock";
import { formatAge, formatDateTime } from "../lib/format";
import {
  auditLevel,
  brokerLevel,
  connectedLevel,
  counterLevel,
  siteStatusLevel,
  type Level,
} from "../lib/status";

const POLL_MS = 3000;
const EVENT_LIMIT = 100;

async function fetchSystem(signal: AbortSignal): Promise<SystemResponse> {
  const out = await api.system(signal);
  noteServerTime(Date.parse(out.generated_at));
  return out;
}

const fetchEvents = (signal: AbortSignal): Promise<EventsResponse> => api.events(EVENT_LIMIT, signal);

function num(v: number | null | undefined): string {
  return v === null || v === undefined || !Number.isFinite(v) ? "--" : String(v);
}

interface TileProps {
  label: string;
  value: string;
  level: Level;
  testId?: string;
  sub?: string;
}

function Tile({ label, value, level, testId, sub }: TileProps) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <Badge level={level} testId={testId} variant="solid" className="tile-value">
        {value}
      </Badge>
      {sub ? <span className="tile-sub">{sub}</span> : null}
    </div>
  );
}

interface CounterProps {
  label: string;
  n: number | null | undefined;
  critical?: boolean;
  testId?: string;
}

function Counter({ label, n, critical = false, testId }: CounterProps) {
  return (
    <li className="counter">
      <span>{label}</span>
      <Badge level={counterLevel(n, critical)} testId={testId}>
        {num(n)}
      </Badge>
    </li>
  );
}

const COMMAND_STATUSES: Array<{ key: string; level: Level }> = [
  { key: "applied", level: "normal" },
  { key: "sent", level: "stale" },
  { key: "partial_failure", level: "warning" },
  { key: "rejected", level: "warning" },
  { key: "expired", level: "warning" },
  { key: "failed", level: "critical" },
];

export function SystemPage() {
  const sys = usePolling(fetchSystem, POLL_MS, "system");
  const ev = usePolling(fetchEvents, POLL_MS, "events");
  const s = sys.data;
  const sec = s?.security;
  const cmd = s?.commands ?? {};
  const ok = Object.entries(cmd)
    .filter(([k]) => k === "applied")
    .reduce((a, [, v]) => a + (Number(v) || 0), 0);
  const failed = Object.entries(cmd)
    .filter(([k]) => k === "failed" || k === "partial_failure")
    .reduce((a, [, v]) => a + (Number(v) || 0), 0);

  return (
    <div className="page" data-testid="system-page">
      <div className="page-head">
        <div>
          <h1>System &amp; security</h1>
          <p className="muted">Platform health, security counters and the event log - refresh every 3 s.</p>
        </div>
      </div>
      <DataBanner error={sys.error} hasData={s !== null} />

      {s ? (
        <StaleContext.Provider value={sys.stale}>
          <div className={sys.stale ? "is-stale" : undefined} data-stale={sys.stale ? "true" : "false"}>
            <div className="tile-grid">
              <Tile
                label="Sites connected"
                value={`${s.sites_connected}/${s.sites_total}`}
                level={connectedLevel(s.sites_connected, s.sites_total)}
                testId="sites-connected"
                sub="ONLINE or DEGRADED"
              />
              <Tile
                label="MQTT broker"
                value={s.broker.status}
                level={brokerLevel(s.broker.status)}
                testId="broker-status"
                sub={`${num(s.broker.clients_connected)} clients - ${num(s.broker.msgs_received_1min)} msg/min in - ${num(s.broker.msgs_sent_1min)} out`}
              />
              <Tile
                label="Audit integrity"
                value={s.audit.integrity || "UNKNOWN"}
                level={auditLevel(s.audit.integrity)}
                testId="audit-integrity"
                sub={`${num(s.audit.rows)} rows - checked ${s.audit.checked_at ? formatDateTime(s.audit.checked_at) : "never"}`}
              />
              <Tile
                label="Historian"
                value={s.historian.connected ? "CONNECTED" : "DISCONNECTED"}
                level={s.historian.connected ? "normal" : "critical"}
                sub={`heartbeat ${formatAge(s.historian.heartbeat_age_s)}`}
              />
              <Tile
                label="Central API MQTT"
                value={s.central_api?.mqtt_connected ? "CONNECTED" : "DISCONNECTED"}
                level={s.central_api ? (s.central_api.mqtt_connected ? "normal" : "critical") : "stale"}
              />
              <Tile
                label="Stale sensors"
                value={num(s.stale_sensors)}
                level={s.stale_sensors > 0 ? "stale" : "normal"}
                testId="stale-sensors"
                sub={`of ${s.sites_total * 2} readings`}
              />
            </div>

            <div className="detail-grid">
              <section className="card">
                <header className="section-head">
                  <h2>Security counters</h2>
                </header>
                <ul className="counter-list">
                  <Counter label="Broker auth failures" n={sec?.broker_auth_failures} testId="sec-broker-auth-failures" />
                  <Counter label="Broker ACL denials" n={sec?.broker_acl_denials_logged} />
                  <Counter label="Login failures" n={sec?.login_failures} testId="sec-login-failures" />
                  <Counter label="Authorization denied" n={sec?.authz_denied} testId="sec-authz-denied" />
                  <Counter label="Event schema rejected" n={sec?.event_schema_rejected} testId="sec-schema-rejected" />
                  <Counter label="Untrusted claims dropped" n={sec?.untrusted_claims_dropped} testId="sec-untrusted-claims" />
                  <Counter label="Topic identity mismatch" n={sec?.topic_identity_mismatch} critical testId="sec-topic-mismatch" />
                  <Counter label="Replay detected" n={sec?.replay_detected} critical testId="sec-replay" />
                  <Counter label="Rate-limited events" n={sec?.rate_limited_events} testId="sec-rate-limited" />
                  <Counter label="Commands rejected" n={sec?.command_rejected} />
                  <Counter label="Commands invalid" n={sec?.command_invalid} />
                </ul>
              </section>

              <section className="card">
                <header className="section-head">
                  <h2>Commands</h2>
                  <span className="muted small">{`${ok} succeeded - ${failed} failed`}</span>
                </header>
                <ul className="counter-list">
                  {COMMAND_STATUSES.map(({ key, level }) => (
                    <li className="counter" key={key}>
                      <span>{key.toUpperCase()}</span>
                      <Badge level={(cmd[key] ?? 0) > 0 ? level : "normal"} testId={`cmd-count-${key}`}>
                        {num(cmd[key] ?? 0)}
                      </Badge>
                    </li>
                  ))}
                </ul>
                <h3 className="sub-head">Ingest</h3>
                <ul className="counter-list">
                  <Counter label="Queue drops (critical)" n={s.ingest.queue_dropped_critical} critical testId="ingest-drops-critical" />
                  <Counter label="Queue drops (normal)" n={s.ingest.queue_dropped_normal} testId="ingest-drops-normal" />
                  <Counter label="Ingest errors" n={s.ingest.errors} />
                  <li className="counter">
                    <span>Late telemetry</span>
                    <span className="mono">{num(s.message_rate.late_telemetry)}</span>
                  </li>
                  <li className="counter">
                    <span>Gateway telemetry ingested</span>
                    <span className="mono">{num(s.message_rate.gateway_telemetry)}</span>
                  </li>
                </ul>
              </section>

              <section className="card">
                <header className="section-head">
                  <h2>Site connectivity</h2>
                </header>
                <ul className="counter-list">
                  {Object.entries(s.site_status ?? {}).map(([site, st]) => (
                    <li className="counter" key={site}>
                      <span>{site}</span>
                      <Badge level={siteStatusLevel(st)}>{String(st)}</Badge>
                    </li>
                  ))}
                </ul>
              </section>
            </div>
          </div>
        </StaleContext.Provider>
      ) : null}

      <section className="card events-card">
        <header className="section-head">
          <h2>Event log</h2>
          <span className="muted small">{`latest ${EVENT_LIMIT} events (alerts, events, security, audit)`}</span>
        </header>
        <DataBanner error={ev.error} hasData={ev.data !== null} />
        <StaleContext.Provider value={ev.stale}>
          <EventsTable events={ev.data?.events} testId="events-table" emptyText={ev.loading ? "Loading..." : "No events."} />
        </StaleContext.Provider>
      </section>
    </div>
  );
}
