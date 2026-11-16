import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ActuatorView, ReadingView, Role, SiteDetail } from "../api/types";
import { AuthProvider } from "../auth/AuthContext";
import { clearSession, setSession } from "../auth/session";
import { SiteCard } from "../components/SiteCard";
import { SiteDetailPage } from "../pages/SiteDetailPage";

const reading = (value: number, level: ReadingView["level"] = "normal"): ReadingView => ({
  value,
  unit: "C",
  fresh: level !== "stale",
  display: level === "stale" ? "STALE" : "FRESH",
  level,
  age_s: 1,
  gateway_ingest_ts: "2026-11-17T10:00:00Z",
});

const act = (display: ActuatorView["display"], state: string | null, group: ActuatorView["group"]): ActuatorView => ({
  state,
  display,
  fresh: display === "ON" || display === "OFF",
  observed_at: "2026-11-17T10:00:00Z",
  quality: "GOOD",
  age_s: 2,
  group,
});

function fixture(): SiteDetail {
  return {
    site_id: "site01",
    display_name: "Greenhouse 01",
    status: "ONLINE",
    mode: "AUTO",
    health: "OK",
    issues: [],
    bridge: "UP",
    temperature: reading(20.4),
    humidity: reading(61.2),
    actuators: {
      fan01: act("ON", "ON", "fan_group"),
      fan02: act("STALE", "ON", "fan_group"),
      dehumidifier01: act("OFF", "OFF", "dehumidifier_group"),
      dehumidifier02: act("UNKNOWN", null, "dehumidifier_group"),
    },
    groups: {},
    data_quality: "GOOD",
    last_updated: "2026-11-17T10:00:00Z",
    active_alerts: 0,
    targets: {
      temperature: 20,
      humidity: 60,
      temperature_band: [20.5, 19.5],
      humidity_band: [62, 58],
      interlock_temperature: 32,
    },
    trend: { temperature: [], humidity: [] },
    sensor_quality: { temperature: { VALIDATED: 10 }, humidity: { VALIDATED: 9, FROZEN: 1 } },
    recent_commands: [
      {
        command_id: "11111111-2222-3333-4444-555555555555",
        group: "fan_group",
        requested: "ON",
        operator: "<b>op</b>",
        reason: "test",
        status: "FAILED",
        detail: null,
        members: { fan01: "ON", fan02: "OFF" },
        source: "REMOTE",
        mode: "MANUAL_OVERRIDE",
        acked_at: "2026-11-17T10:00:01Z",
        created_ts: "2026-11-17T10:00:00Z",
      },
    ],
    recent_alerts: [],
    gateway: { heartbeat: { seq: 4 }, heartbeat_age_s: 3, bridge_state: "1", bridge_ts: null },
  };
}

function renderDetail(role: Role) {
  setSession({ token: "t", role, username: role, expiresAt: Date.now() + 60_000 });
  return render(
    <MemoryRouter initialEntries={["/sites/site01"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <Routes>
          <Route path="/sites/:siteId" element={<SiteDetailPage />} />
          <Route path="/login" element={<p>login</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(fixture()), { status: 200, headers: { "content-type": "application/json" } })),
  );
});

afterEach(() => {
  cleanup();
  clearSession();
  vi.unstubAllGlobals();
});

describe("site detail role gating", () => {
  it("viewer sees data but no control panel", async () => {
    renderDetail("viewer");
    expect(await screen.findByTestId("commands-table")).toBeTruthy();
    expect(screen.getByTestId("trend-chart")).toBeTruthy();
    expect(screen.getByTestId("alerts-table")).toBeTruthy();
    expect(screen.queryByTestId("control-panel")).toBeNull();
    expect(screen.queryByTestId("cmd-fan-on")).toBeNull();
    expect(screen.getByTestId("site-mode").textContent).toBe("AUTO");
  });

  it("operator sees the control panel with all controls", async () => {
    renderDetail("operator");
    const panel = await screen.findByTestId("control-panel");
    for (const id of ["cmd-reason", "cmd-fan-on", "cmd-fan-off", "cmd-dehum-on", "cmd-dehum-off", "mode-auto", "mode-override", "mode-maintenance"]) {
      expect(within(panel).getByTestId(id)).toBeTruthy();
    }
  });

  it("sends the bearer token and renders command status colours", async () => {
    renderDetail("viewer");
    const table = await screen.findByTestId("commands-table");
    const status = within(table).getByTestId("command-status");
    expect(status.textContent).toBe("FAILED");
    expect(status.getAttribute("data-level")).toBe("critical");
    expect(table.querySelector("b")).toBeNull(); // operator name with markup is text
    const call = vi.mocked(fetch).mock.calls[0];
    expect(String(call[0])).toBe("/api/sites/site01");
    expect(new Headers((call[1] as RequestInit).headers).get("Authorization")).toBe("Bearer t");
  });
});

describe("SiteCard", () => {
  it("renders test ids and shows a retained stale ON as gray STALE", () => {
    render(<SiteCard site={fixture()} nowMs={Date.parse("2026-11-17T10:00:05Z")} onOpen={() => undefined} />);
    const card = screen.getByTestId("site-card-site01");
    const status = within(card).getByTestId("site-status");
    expect(status.textContent).toBe("ONLINE");
    expect(status.getAttribute("data-level")).toBe("normal");
    expect(within(card).getByTestId("site-mode").textContent).toBe("AUTO");

    const fan01 = within(card).getByTestId("actuator-fan01");
    expect(fan01.textContent).toBe("ON");
    expect(fan01.getAttribute("data-level")).toBe("normal");

    const fan02 = within(card).getByTestId("actuator-fan02");
    expect(fan02.textContent).toBe("STALE (last: ON)");
    expect(fan02.getAttribute("data-level")).toBe("stale");

    expect(within(card).getByTestId("actuator-dehumidifier01").textContent).toBe("OFF");
    expect(within(card).getByTestId("actuator-dehumidifier02").getAttribute("data-level")).toBe("stale");
    expect(within(card).getByTestId("temp-value").textContent).toContain("20.4");
    expect(within(card).getByTestId("hum-value").textContent).toContain("61.2");
    expect(within(card).getByTestId("data-quality").textContent).toBe("GOOD");
    expect(within(card).getByTestId("alert-count").textContent).toBe("0");
    expect(card.textContent).toContain("Updated 5 s ago");
  });
});
