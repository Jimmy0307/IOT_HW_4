// Pure mappings from API values to the 4-level colour semantics.
// normal = green, warning = amber, critical = red, stale = gray.
export type Level = "normal" | "warning" | "critical" | "stale";

export const LEVEL_CLASS: Record<Level, string> = {
  normal: "lvl-normal",
  warning: "lvl-warning",
  critical: "lvl-critical",
  stale: "lvl-stale",
};

export function levelClass(level: Level): string {
  return LEVEL_CLASS[level];
}

export function siteStatusLevel(status: string | null | undefined): Level {
  switch (status) {
    case "ONLINE":
      return "normal";
    case "DEGRADED":
      return "warning";
    case "OFFLINE":
      return "critical";
    default:
      return "stale";
  }
}

/** Actuator / group display. Only a FRESH ON/OFF is coloured; STALE/UNKNOWN are always gray. */
export function actuatorLevel(display: string | null | undefined, quality?: string | null): Level {
  if (display === "ON" || display === "OFF") return quality === "BAD" ? "warning" : "normal";
  if (display === "MIXED") return "warning";
  return "stale";
}

/** Text shown for an actuator; a retained-but-stale value is never shown as a bare "ON". */
export function actuatorLabel(display: string | null | undefined, lastState: string | null | undefined): string {
  if (display === "ON" || display === "OFF") return display;
  if (display === "STALE") return lastState ? `STALE (last: ${lastState})` : "STALE";
  if (display === "MIXED") return "MIXED";
  return "UNKNOWN";
}

export function readingLevel(level: string | null | undefined): Level {
  switch (level) {
    case "normal":
      return "normal";
    case "warning":
      return "warning";
    case "critical":
      return "critical";
    default:
      return "stale";
  }
}

export function dataQualityLevel(q: string | null | undefined): Level {
  if (q === "GOOD") return "normal";
  if (q === "SUSPECT") return "warning";
  return "stale";
}

export function commandStatusLevel(status: string | null | undefined): Level {
  switch (status) {
    case "APPLIED":
      return "normal";
    case "PARTIAL_FAILURE":
    case "REJECTED":
    case "EXPIRED":
      return "warning";
    case "FAILED":
      return "critical";
    default: // SENT (pending ack), DUPLICATE, unknown
      return "stale";
  }
}

export function severityLevel(severity: string | null | undefined, eventType?: string | null): Level {
  if (eventType === "ACTUATOR_FAILURE") return "critical";
  if (eventType === "ACTUATOR_PARTIAL_FAILURE") return "warning";
  switch (severity) {
    case "INFO":
    case "NOTICE":
      return "normal";
    case "WARNING":
      return "warning";
    case "CRITICAL":
    case "SECURITY":
      return "critical";
    default:
      return "stale";
  }
}

export function auditLevel(integrity: string | null | undefined): Level {
  if (integrity === "PASS") return "normal";
  if (integrity === "FAIL") return "critical";
  return "stale";
}

export function bridgeLevel(bridge: string | null | undefined): Level {
  if (bridge === "UP") return "normal";
  if (bridge === "DOWN") return "critical";
  return "stale";
}

export function brokerLevel(status: string | null | undefined): Level {
  if (status === "UP") return "normal";
  if (status === "DOWN") return "critical";
  return "stale";
}

export function modeLevel(mode: string | null | undefined): Level {
  switch (mode) {
    case "AUTO":
      return "normal";
    case "MANUAL_OVERRIDE":
    case "MAINTENANCE":
      return "warning";
    case "FAILSAFE":
      return "critical";
    default:
      return "stale";
  }
}

export function healthLevel(health: string | null | undefined): Level {
  if (health === "OK") return "normal";
  if (health === "DEGRADED") return "warning";
  return "stale";
}

/** A security counter is fine at 0 and a warning (or critical) when non-zero. */
export function counterLevel(n: number | null | undefined, critical = false): Level {
  if (n === null || n === undefined || !Number.isFinite(n)) return "stale";
  if (n <= 0) return "normal";
  return critical ? "critical" : "warning";
}

export function connectedLevel(connected: number, total: number): Level {
  if (total <= 0) return "stale";
  if (connected >= total) return "normal";
  if (connected === 0) return "critical";
  return "warning";
}
