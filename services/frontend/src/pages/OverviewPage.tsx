import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { OverviewResponse } from "../api/types";
import { StaleContext } from "../components/Badge";
import { DataBanner } from "../components/DataBanner";
import { SiteCard } from "../components/SiteCard";
import { usePolling } from "../hooks/usePolling";
import { noteServerTime, serverNow } from "../lib/clock";
import { formatAge, ageSecondsFrom } from "../lib/format";

const POLL_MS = 2000;

async function fetchOverview(signal: AbortSignal): Promise<OverviewResponse> {
  const out = await api.overview(signal);
  noteServerTime(Date.parse(out.generated_at));
  return out;
}

export function OverviewPage() {
  const navigate = useNavigate();
  const { data, error, stale, loading } = usePolling(fetchOverview, POLL_MS, "overview");
  const open = useCallback((siteId: string) => navigate(`/sites/${encodeURIComponent(siteId)}`), [navigate]);
  const nowMs = serverNow();
  const sites = data?.sites ?? [];
  const count = (s: string) => sites.filter((x) => x.status === s).length;

  return (
    <div className="page" data-testid="overview-page">
      <div className="page-head">
        <div>
          <h1>Global overview</h1>
          <p className="muted">
            {data
              ? `${sites.length} sites - snapshot ${formatAge(ageSecondsFrom(data.generated_at, nowMs))} - refresh every 2 s`
              : "Loading site status..."}
          </p>
        </div>
        {data ? (
          <div className="summary-pills" aria-label="Site status summary">
            <span className="pill lvl-normal" data-level="normal">{`${count("ONLINE")} ONLINE`}</span>
            <span className="pill lvl-warning" data-level="warning">{`${count("DEGRADED")} DEGRADED`}</span>
            <span className="pill lvl-critical" data-level="critical">{`${count("OFFLINE")} OFFLINE`}</span>
            <span className="pill lvl-stale" data-level="stale">{`${count("UNKNOWN")} UNKNOWN`}</span>
          </div>
        ) : null}
      </div>
      <DataBanner error={error} hasData={data !== null} />
      {loading && !data ? <p className="muted">Loading...</p> : null}
      <StaleContext.Provider value={stale}>
        <div className={`site-grid${stale ? " is-stale" : ""}`} data-stale={stale ? "true" : "false"}>
          {sites.map((s) => (
            <SiteCard key={s.site_id} site={s} nowMs={nowMs} onOpen={open} />
          ))}
        </div>
      </StaleContext.Provider>
    </div>
  );
}
