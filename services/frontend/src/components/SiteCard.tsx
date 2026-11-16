import type { KeyboardEvent } from "react";
import type { SiteSummary } from "../api/types";
import { ageSecondsFrom, formatAge, formatNumber } from "../lib/format";
import { bridgeLevel, dataQualityLevel, healthLevel, modeLevel, siteStatusLevel } from "../lib/status";
import { ActuatorChip } from "./ActuatorChip";
import { Badge, useEffectiveLevel } from "./Badge";
import { Reading } from "./Reading";

export const ACTUATOR_IDS = ["fan01", "fan02", "dehumidifier01", "dehumidifier02"] as const;
export const SITE_ID_RE = /^[A-Za-z0-9_-]{1,32}$/;

interface SiteCardProps {
  site: SiteSummary;
  nowMs: number;
  onOpen: (siteId: string) => void;
}

export function SiteCard({ site, nowMs, onOpen }: SiteCardProps) {
  const valid = SITE_ID_RE.test(site.site_id);
  const open = () => {
    if (valid) onOpen(site.site_id);
  };
  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  };
  const t = site.targets;
  const level = useEffectiveLevel(siteStatusLevel(site.status));
  const alerts = Number.isFinite(site.active_alerts) ? site.active_alerts : 0;
  return (
    <article
      className={`card site-card status-${level}`}
      data-level={level}
      data-testid={valid ? `site-card-${site.site_id}` : undefined}
      role="link"
      tabIndex={0}
      aria-label={`${site.display_name} - ${site.status}. Open site detail`}
      onClick={open}
      onKeyDown={onKey}
    >
      <header className="site-card-head">
        <div>
          <h2 className="site-name">{site.display_name}</h2>
          <span className="site-id">{site.site_id}</span>
        </div>
        <Badge level={siteStatusLevel(site.status)} testId="site-status" variant="solid">
          {site.status}
        </Badge>
      </header>

      <div className="site-card-meta">
        <span className="meta-item">
          <span className="meta-label">Mode</span>
          <Badge level={modeLevel(site.mode)} testId="site-mode">
            {site.mode ?? "UNKNOWN"}
          </Badge>
        </span>
        <span className="meta-item">
          <span className="meta-label">Health</span>
          <Badge level={healthLevel(site.health)}>{site.health ?? "UNKNOWN"}</Badge>
        </span>
        <span className="meta-item">
          <span className="meta-label">Bridge</span>
          <Badge level={bridgeLevel(site.bridge)}>{site.bridge}</Badge>
        </span>
      </div>

      <div className="readings">
        <Reading
          label="Temperature"
          unit="°C"
          reading={site.temperature}
          testId="temp-value"
          hint={t ? `target ${formatNumber(t.temperature)} °C` : undefined}
        />
        <Reading
          label="Humidity"
          unit="%RH"
          reading={site.humidity}
          testId="hum-value"
          hint={t ? `target ${formatNumber(t.humidity, 0)} %RH` : undefined}
        />
      </div>

      <div className="actuators">
        {ACTUATOR_IDS.map((id) => (
          <ActuatorChip key={id} id={id} view={site.actuators?.[id]} />
        ))}
      </div>

      <footer className="site-card-foot">
        <span className="meta-item">
          <span className="meta-label">Data</span>
          <Badge level={dataQualityLevel(site.data_quality)} testId="data-quality">
            {site.data_quality}
          </Badge>
        </span>
        <span className="meta-item">
          <span className="meta-label">Alerts</span>
          <Badge level={alerts > 0 ? "critical" : "normal"} testId="alert-count">
            {String(alerts)}
          </Badge>
        </span>
        <span className="updated" title={site.last_updated ?? "never"}>
          {`Updated ${formatAge(ageSecondsFrom(site.last_updated, nowMs))}`}
        </span>
      </footer>
    </article>
  );
}
