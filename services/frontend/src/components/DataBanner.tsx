import type { ApiError } from "../api/client";

interface DataBannerProps {
  error: ApiError | null;
  hasData: boolean;
}

/** Gray banner for "data not live" conditions; red only for unexpected API errors. */
export function DataBanner({ error, hasData }: DataBannerProps) {
  if (!error || error.status === 401) return null;
  const suffix = hasData ? " Showing last received data, marked STALE." : " No data received yet.";
  let text: string;
  let level: "stale" | "critical" = "stale";
  if (error.status === 503) {
    text = `Historian data unavailable.${suffix}`;
  } else if (error.status === 0) {
    text = `Central API unreachable.${suffix}`;
  } else {
    level = "critical";
    text = `API error (${error.status}): ${error.message}.${suffix}`;
  }
  return (
    <div className={`banner banner-${level}`} role="status" data-testid="data-banner" data-level={level}>
      <strong>{level === "stale" ? "STALE" : "ERROR"}</strong>
      <span>{text}</span>
    </div>
  );
}
