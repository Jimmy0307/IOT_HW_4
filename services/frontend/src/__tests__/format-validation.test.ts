import { describe, expect, it } from "vitest";
import { buildPath, toSeries, yDomain } from "../components/TrendChart";
import { ageSecondsFrom, band, formatAge, formatDetails, formatNumber, truncate } from "../lib/format";
import { REASON_PATTERN, validateReason, validateTtl } from "../lib/validation";

describe("formatAge", () => {
  it.each([
    [0, "0 s ago"],
    [4.9, "4 s ago"],
    [59, "59 s ago"],
    [60, "1 min ago"],
    [3599, "59 min ago"],
    [3600, "1 h ago"],
    [86400 * 2, "2 d ago"],
    [-5, "0 s ago"],
  ])("%s s -> %s", (s, expected) => {
    expect(formatAge(s)).toBe(expected);
  });

  it("unknown age reads 'never'", () => {
    expect(formatAge(null)).toBe("never");
    expect(formatAge(undefined)).toBe("never");
    expect(formatAge(Number.NaN)).toBe("never");
  });
});

describe("ageSecondsFrom", () => {
  it("computes age against a reference clock", () => {
    const now = Date.parse("2026-11-17T10:00:30Z");
    expect(ageSecondsFrom("2026-11-17T10:00:00Z", now)).toBe(30);
    expect(ageSecondsFrom("2026-11-17T10:00:00+00:00", now)).toBe(30);
  });
  it("returns null for missing or invalid timestamps", () => {
    expect(ageSecondsFrom(null, 0)).toBeNull();
    expect(ageSecondsFrom("not a date", 0)).toBeNull();
  });
});

describe("formatting", () => {
  it("formats numbers and placeholders", () => {
    expect(formatNumber(21.456)).toBe("21.5");
    expect(formatNumber(60, 0)).toBe("60");
    expect(formatNumber(null)).toBe("--");
  });

  it("formats event details as key: value text", () => {
    expect(formatDetails({ code: "BAD_JSON", n: 2, ok: false, obj: { a: 1 } })).toBe(
      'code: BAD_JSON; n: 2; ok: false; obj: {"a":1}',
    );
    expect(formatDetails(null)).toBe("");
    expect(formatDetails("plain")).toBe("plain");
  });

  it("truncates long text to the limit", () => {
    const out = truncate("x".repeat(500), 200);
    expect(out.length).toBe(200);
    expect(out.endsWith("…")).toBe(true);
    expect(truncate("short", 200)).toBe("short");
  });

  it("normalises a [high, low] band", () => {
    expect(band([20.5, 19.5])).toEqual({ low: 19.5, high: 20.5 });
    expect(band([58, 62])).toEqual({ low: 58, high: 62 });
    expect(band([])).toBeNull();
    expect(band(undefined)).toBeNull();
  });
});

describe("validateReason", () => {
  it.each(["lab test", "humidity high: run fans", "op-1_check.2", "a".repeat(64)])("accepts %s", (r) => {
    expect(validateReason(r).ok).toBe(true);
  });

  it.each([
    ["", "required"],
    ["a".repeat(65), "at most"],
    ["<script>", "Only"],
    ["drop;table", "Only"],
    ["new\nline", "Only"],
    ["emoji \u{1F600}", "Only"],
    ["   ", "spaces"],
  ])("rejects %j", (r, msg) => {
    const res = validateReason(r);
    expect(res.ok).toBe(false);
    expect(res.error).toContain(msg);
  });

  it("matches the server pattern exactly", () => {
    expect(REASON_PATTERN.source).toBe("^[A-Za-z0-9_.:\\- ]{1,64}$");
  });
});

describe("validateTtl", () => {
  it("accepts whole seconds within range", () => {
    expect(validateTtl("300")).toEqual({ ok: true, error: null, value: 300 });
    expect(validateTtl("86400").ok).toBe(true);
  });
  it.each(["0", "86401", "-5", "1.5", "abc", ""])("rejects %j", (v) => {
    expect(validateTtl(v).ok).toBe(false);
  });
});

describe("trend chart helpers", () => {
  const end = Date.parse("2026-11-17T10:10:00Z");
  const start = end - 600_000;

  it("keeps only valid points within the window, sorted", () => {
    const s = toSeries(
      [
        { t: "2026-11-17T10:05:00Z", v: 21, q: "GOOD" },
        { t: "2026-11-17T09:00:00Z", v: 99, q: "GOOD" },
        { t: "bad", v: 1, q: null },
        { t: "2026-11-17T10:01:00Z", v: 20, q: "GOOD" },
      ],
      start,
      end,
    );
    expect(s.map((p) => p.v)).toEqual([20, 21]);
  });

  it("pads the y domain and includes reference lines", () => {
    const d = yDomain([20, 21], [19.5, 20.5]);
    expect(d.min).toBeLessThan(19.5);
    expect(d.max).toBeGreaterThan(21);
    expect(yDomain([], [])).toEqual({ min: 0, max: 1 });
  });

  it("breaks the line on gaps longer than 15 s", () => {
    const pts = [
      { t: 0, v: 1 },
      { t: 1000, v: 2 },
      { t: 60_000, v: 3 },
    ];
    const path = buildPath(pts, (t) => t / 1000, (v) => v);
    expect(path.match(/M/g)).toHaveLength(2);
    expect(path.match(/L/g)).toHaveLength(1);
  });
});
