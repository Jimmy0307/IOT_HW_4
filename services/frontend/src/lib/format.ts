// Pure formatting helpers. Output is always plain text for React text nodes.

export const DETAILS_MAX_CHARS = 200;

/** Seconds between an ISO timestamp and `nowMs`; null when unknown/unparseable. */
export function ageSecondsFrom(ts: string | null | undefined, nowMs: number): number | null {
  if (!ts) return null;
  const t = Date.parse(ts);
  if (!Number.isFinite(t)) return null;
  return Math.max(0, (nowMs - t) / 1000);
}

/** Human relative age: "3 s ago", "4 min ago", "2 h ago", "3 d ago", or "never". */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "never";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function formatNumber(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || typeof v !== "number" || !Number.isFinite(v)) return "--";
  return v.toFixed(digits);
}

export function formatTime(ts: string | null | undefined): string {
  if (!ts) return "--";
  const t = Date.parse(ts);
  if (!Number.isFinite(t)) return String(ts).slice(0, 40);
  return new Date(t).toLocaleTimeString([], { hour12: false });
}

export function formatDateTime(ts: string | null | undefined): string {
  if (!ts) return "--";
  const t = Date.parse(ts);
  if (!Number.isFinite(t)) return String(ts).slice(0, 40);
  const d = new Date(t);
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour12: false })}`;
}

export function truncate(text: string, max: number = DETAILS_MAX_CHARS): string {
  return text.length > max ? `${text.slice(0, Math.max(0, max - 1))}…` : text;
}

function stringifyValue(v: unknown): string {
  if (typeof v === "string") return v;
  if (v === null || v === undefined) return String(v);
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v) ?? String(v);
  } catch {
    return "[unserializable]";
  }
}

/**
 * Render untrusted event `details` as "key: value; key: value" plain text,
 * truncated to DETAILS_MAX_CHARS. The caller must render it as a text node.
 */
export function formatDetails(details: unknown, max: number = DETAILS_MAX_CHARS): string {
  if (details === null || details === undefined) return "";
  if (typeof details !== "object" || Array.isArray(details)) return truncate(stringifyValue(details), max);
  const parts = Object.entries(details as Record<string, unknown>).map(([k, v]) => `${k}: ${stringifyValue(v)}`);
  return truncate(parts.join("; "), max);
}

/** Normalise a two-value band into {low, high}; the API serialises thresholds as [high, low]. */
export function band(values: readonly number[] | null | undefined): { low: number; high: number } | null {
  if (!values || values.length < 2) return null;
  const nums = values.filter((n) => typeof n === "number" && Number.isFinite(n));
  if (nums.length < 2) return null;
  return { low: Math.min(...nums), high: Math.max(...nums) };
}

export function humanize(code: string | null | undefined): string {
  if (!code) return "--";
  return code.replace(/_/g, " ");
}
