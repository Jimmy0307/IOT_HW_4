import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { SiteDetail } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ActuatorChip } from "../components/ActuatorChip";
import { Badge, StaleContext } from "../components/Badge";
import { CommandsTable } from "../components/CommandsTable";
import { ControlPanel } from "../components/ControlPanel";
import { DataBanner } from "../components/DataBanner";
import { EventsTable } from "../components/EventsTable";
import { Reading } from "../components/Reading";
import { ACTUATOR_IDS, SITE_ID_RE } from "../components/SiteCard";
import { TrendChart } from "../components/TrendChart";
import { usePolling } from "../hooks/usePolling";
import { serverNow } from "../lib/clock";
import { ageSecondsFrom, band, formatAge, formatNumber, humanize } from "../lib/format";
import {
  bridgeLevel,
  dataQualityLevel,
  healthLevel,
  modeLevel,
  siteStatusLevel,
  type Level,
} from "../lib/status";

const POLL_MS = 2000;
const QUALITY_ORDER = ["VALIDATED", "FROZEN", "SPIKE_PENDING", "STALE", "RANGE_FAIL", "TIMESTAMP_FUTURE", "REPLAY_OR_DUPLICATE"];
const SUSPECT_RESULTS = new Set(["FROZEN", "SPIKE_PENDING"]);

/** VALIDATED = green, SUSPECT results = amber, STALE = gray, BAD/rejected results = red. */
function qualityLevel(result: string): Level {
  if (result === "VALIDATED") return "normal";
  if (result === "STALE") return "stale";
  if (SUSPECT_RESULTS.has(result)) return "warning";
  return "critical";
}

function QualityCounts({ title, counts }: { title: string; counts: Record<string, number> | undefined }) {
  const rank = (r: string) => {
    const i = QUALITY_ORDER.indexOf(r);
    return i < 0 ? QUALITY_ORDER.length : i;
  };
  const entries = Object.entries(counts ?? {}).sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
  const total = entries.reduce((s, [, n]) => s + (Number(n) || 0), 0);
  return (
    <div className="quality-block">
      <h3>{title}</h3>
      {entries.length === 0 ? (
        <p className="muted">No samples.</p>
      ) : (
        <ul className="quality-list">
          {entries.map(([result, n]) => (
            <li key={result}>
              <Badge level={qualityLevel(result)}>{humanize(result)}</Badge>
              <span className="quality-count">{String(n)}</span>
              <span className="quality-bar" aria-hidden="true">
                <span
                  className={`quality-fill lvl-${qualityLevel(result)}`}
                  data-level={qualityLevel(result)}
                  style={{ width: `${total > 0 ? Math.round((Number(n) / total) * 100) : 0}%` }}
                />
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">{`last ${total} samples`}</p>
    </div>
  );
}

function hbString(hb: Record<string, unknown> | null | undefined, key: string): string | null {
  const v = hb?.[key];
  return typeof v === "string" || typeof v === "number" ? String(v) : null;
}

export function SiteDetailPage() {
  const { siteId = "" } = useParams();
  const { session } = useAuth();
  const valid = SITE_ID_RE.test(siteId);
  const fetcher = useCallback((signal: AbortSignal) => api.site(siteId, signal), [siteId]);
  const { data, error, stale, refresh } = usePolling<SiteDetail>(fetcher, POLL_MS, `site:${siteId}`);
  const [lastCommandId, setLastCommandId] = useState<string | null>(null);

  if (!valid) {
    return (
      <div className="page">
        <p className="banner banner-critical" data-level="critical">
          Invalid site identifier.
        </p>
        <Link to="/">Back to overview</Link>
      </div>
    );
  }

  const nowMs = serverNow();
  const d = data;
  const tBand = band(d?.targets?.temperature_band);
  const hBand = band(d?.targets?.humidity_band);
  const hb = d?.gateway?.heartbeat ?? null;
  const isOperator = session?.role === "operator";
  const notFound = error?.status === 404;

  return (
    <div className="page" data-testid="site-detail-page">
      <div className="page-head">
        <div>
          <Link to="/" className="back-link">
            &larr; All sites
          </Link>
          <h1>{d ? `${d.display_name}` : siteId}</h1>
          <p className="muted">
            {d
              ? `${d.site_id} - updated ${formatAge(ageSecondsFrom(d.last_updated, nowMs))} - refresh every 2 s`
              : notFound
                ? "Unknown site."
                : "Loading..."}
          </p>
        </div>
        {d ? (
          <StaleContext.Provider value={stale}>
            <div className="head-badges">
              <span className="meta-item">
                <span className="meta-label">Status</span>
                <Badge level={siteStatusLevel(d.status)} testId="site-status" variant="solid">
                  {d.status}
                </Badge>
              </span>
              <span className="meta-item">
                <span className="meta-label">Mode</span>
                <Badge level={modeLevel(d.mode)} testId="site-mode">
                  {d.mode ?? "UNKNOWN"}
                </Badge>
              </span>
              <span className="meta-item">
                <span className="meta-label">Data</span>
                <Badge level={dataQualityLevel(d.data_quality)} testId="data-quality">
                  {d.data_quality}
                </Badge>
              </span>
            </div>
          </StaleContext.Provider>
        ) : null}
      </div>

      <DataBanner error={notFound ? null : error} hasData={data !== null} />

      {d ? (
        <StaleContext.Provider value={stale}>
          <div className={`detail-grid${stale ? " is-stale" : ""}`} data-stale={stale ? "true" : "false"}>
            <section className="card span-2">
              <header className="section-head">
                <h2>Live conditions</h2>
              </header>
              <div className="readings readings-lg">
                <Reading
                  label="Temperature"
                  unit="°C"
                  size="lg"
                  reading={d.temperature}
                  testId="temp-value"
                  hint={`target ${formatNumber(d.targets?.temperature)} °C, band ${formatNumber(tBand?.low)}-${formatNumber(tBand?.high)}, interlock ${formatNumber(d.targets?.interlock_temperature)}`}
                />
                <Reading
                  label="Humidity"
                  unit="%RH"
                  size="lg"
                  reading={d.humidity}
                  testId="hum-value"
                  hint={`target ${formatNumber(d.targets?.humidity, 0)} %RH, band ${formatNumber(hBand?.low)}-${formatNumber(hBand?.high)}`}
                />
              </div>
              <TrendChart
                temperature={d.trend?.temperature}
                humidity={d.trend?.humidity}
                tempBand={tBand}
                humBand={hBand}
                tempTarget={d.targets?.temperature}
                humTarget={d.targets?.humidity}
                interlock={d.targets?.interlock_temperature}
                endMs={nowMs}
                stale={stale}
              />
            </section>

            <section className="card">
              <header className="section-head">
                <h2>Actuators</h2>
              </header>
              <div className="actuators actuators-detail">
                {ACTUATOR_IDS.map((id) => (
                  <ActuatorChip key={id} id={id} view={d.actuators?.[id]} showAge />
                ))}
              </div>
              <p className="muted small">
                A retained state older than the freshness window, or from an offline site, is shown gray as STALE with
                its last known value.
              </p>
            </section>

            <section className="card">
              <header className="section-head">
                <h2>Gateway</h2>
              </header>
              <dl className="kv">
                <dt>Site status</dt>
                <dd>
                  <Badge level={siteStatusLevel(d.status)}>{d.status}</Badge>
                </dd>
                <dt>Health</dt>
                <dd>
                  <Badge level={healthLevel(d.health)}>{d.health ?? "UNKNOWN"}</Badge>
                </dd>
                <dt>Heartbeat</dt>
                <dd>{formatAge(d.gateway?.heartbeat_age_s ?? null)}</dd>
                <dt>Bridge</dt>
                <dd>
                  <Badge level={bridgeLevel(d.bridge)}>{d.bridge}</Badge>
                </dd>
                <dt>Mode</dt>
                <dd>
                  <Badge level={modeLevel(d.mode)}>{d.mode ?? "UNKNOWN"}</Badge>
                </dd>
                <dt>HB sequence</dt>
                <dd className="mono">{hbString(hb, "seq") ?? "--"}</dd>
                <dt>Issues</dt>
                <dd>
                  {d.issues.length === 0 ? (
                    <span className="muted">none</span>
                  ) : (
                    <span className="issue-list">
                      {d.issues.map((i) => (
                        <Badge key={i} level="warning">
                          {humanize(i)}
                        </Badge>
                      ))}
                    </span>
                  )}
                </dd>
                <dt>Active alerts</dt>
                <dd>
                  <Badge level={d.active_alerts > 0 ? "critical" : "normal"} testId="alert-count">
                    {String(d.active_alerts)}
                  </Badge>
                </dd>
              </dl>
            </section>

            <section className="card">
              <header className="section-head">
                <h2>Sensor quality</h2>
                <span className="muted small">validation result, last 200 samples</span>
              </header>
              <div className="quality-grid">
                <QualityCounts title="Temperature" counts={d.sensor_quality?.temperature} />
                <QualityCounts title="Humidity" counts={d.sensor_quality?.humidity} />
              </div>
            </section>

            {isOperator ? (
              <div className="span-3">
                <ControlPanel
                  siteId={d.site_id}
                  currentMode={d.mode}
                  recentCommands={d.recent_commands}
                  onSent={(id) => {
                    setLastCommandId(id);
                    refresh();
                  }}
                />
              </div>
            ) : null}

            <section className="card span-3">
              <header className="section-head">
                <h2>Recent commands</h2>
                <span className="muted small">last 20, newest first</span>
              </header>
              <CommandsTable commands={d.recent_commands} highlightId={lastCommandId} />
            </section>

            <section className="card span-3">
              <header className="section-head">
                <h2>Recent alerts &amp; security events</h2>
              </header>
              <EventsTable events={d.recent_alerts} testId="alerts-table" showSite={false} emptyText="No alerts." />
            </section>
          </div>
        </StaleContext.Provider>
      ) : null}
    </div>
  );
}
