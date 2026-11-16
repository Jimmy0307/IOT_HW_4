import { describe, expect, it } from "vitest";
import {
  actuatorLabel,
  actuatorLevel,
  auditLevel,
  bridgeLevel,
  commandStatusLevel,
  connectedLevel,
  counterLevel,
  dataQualityLevel,
  levelClass,
  modeLevel,
  readingLevel,
  severityLevel,
  siteStatusLevel,
} from "../lib/status";

describe("siteStatusLevel", () => {
  it.each([
    ["ONLINE", "normal"],
    ["DEGRADED", "warning"],
    ["OFFLINE", "critical"],
    ["UNKNOWN", "stale"],
    [null, "stale"],
    ["garbage", "stale"],
  ])("%s -> %s", (input, expected) => {
    expect(siteStatusLevel(input)).toBe(expected);
  });
});

describe("actuator display", () => {
  it("fresh ON/OFF are normal (green)", () => {
    expect(actuatorLevel("ON")).toBe("normal");
    expect(actuatorLevel("OFF")).toBe("normal");
  });

  it("STALE and UNKNOWN are always gray, never green", () => {
    expect(actuatorLevel("STALE")).toBe("stale");
    expect(actuatorLevel("UNKNOWN")).toBe("stale");
    expect(actuatorLevel(undefined)).toBe("stale");
  });

  it("a fresh state that disagrees with the command is amber", () => {
    expect(actuatorLevel("ON", "BAD")).toBe("warning");
  });

  it("labels a retained stale ON as STALE (last: ON), not ON", () => {
    expect(actuatorLabel("STALE", "ON")).toBe("STALE (last: ON)");
    expect(actuatorLabel("STALE", null)).toBe("STALE");
    expect(actuatorLabel("ON", "ON")).toBe("ON");
    expect(actuatorLabel("UNKNOWN", null)).toBe("UNKNOWN");
  });
});

describe("other mappings", () => {
  it("reading levels", () => {
    expect(readingLevel("normal")).toBe("normal");
    expect(readingLevel("warning")).toBe("warning");
    expect(readingLevel("critical")).toBe("critical");
    expect(readingLevel("stale")).toBe("stale");
    expect(readingLevel("unknown")).toBe("stale");
  });

  it("data quality", () => {
    expect(dataQualityLevel("GOOD")).toBe("normal");
    expect(dataQualityLevel("SUSPECT")).toBe("warning");
    expect(dataQualityLevel("STALE")).toBe("stale");
    expect(dataQualityLevel("UNKNOWN")).toBe("stale");
  });

  it.each([
    ["APPLIED", "normal"],
    ["PARTIAL_FAILURE", "warning"],
    ["REJECTED", "warning"],
    ["EXPIRED", "warning"],
    ["FAILED", "critical"],
    ["SENT", "stale"],
    ["DUPLICATE", "stale"],
  ])("command status %s -> %s", (input, expected) => {
    expect(commandStatusLevel(input)).toBe(expected);
  });

  it("event severity with actuator overrides", () => {
    expect(severityLevel("INFO")).toBe("normal");
    expect(severityLevel("WARNING")).toBe("warning");
    expect(severityLevel("CRITICAL")).toBe("critical");
    expect(severityLevel("SECURITY")).toBe("critical");
    expect(severityLevel("CRITICAL", "ACTUATOR_PARTIAL_FAILURE")).toBe("warning");
    expect(severityLevel("WARNING", "ACTUATOR_FAILURE")).toBe("critical");
  });

  it("audit integrity PASS green / FAIL red / other gray", () => {
    expect(auditLevel("PASS")).toBe("normal");
    expect(auditLevel("FAIL")).toBe("critical");
    expect(auditLevel("UNKNOWN")).toBe("stale");
    expect(auditLevel(null)).toBe("stale");
  });

  it("bridge, mode, counters, connectivity", () => {
    expect(bridgeLevel("UP")).toBe("normal");
    expect(bridgeLevel("DOWN")).toBe("critical");
    expect(bridgeLevel("UNKNOWN")).toBe("stale");
    expect(modeLevel("AUTO")).toBe("normal");
    expect(modeLevel("MANUAL_OVERRIDE")).toBe("warning");
    expect(modeLevel("FAILSAFE")).toBe("critical");
    expect(modeLevel(null)).toBe("stale");
    expect(counterLevel(0)).toBe("normal");
    expect(counterLevel(3)).toBe("warning");
    expect(counterLevel(3, true)).toBe("critical");
    expect(counterLevel(null)).toBe("stale");
    expect(connectedLevel(6, 6)).toBe("normal");
    expect(connectedLevel(4, 6)).toBe("warning");
    expect(connectedLevel(0, 6)).toBe("critical");
  });

  it("level -> CSS class", () => {
    expect(levelClass("normal")).toBe("lvl-normal");
    expect(levelClass("warning")).toBe("lvl-warning");
    expect(levelClass("critical")).toBe("lvl-critical");
    expect(levelClass("stale")).toBe("lvl-stale");
  });
});
