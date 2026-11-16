import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { EventRow } from "../api/types";
import { EventRowView, EventsTable } from "../components/EventsTable";

const SCRIPT = "<script>alert(1)</script>";
const IMG = "<img src=x onerror=alert(1)>";

function makeEvent(message: string, extra: Partial<EventRow> = {}): EventRow {
  return {
    id: 1,
    ingest_ts: "2026-11-17T10:00:00.000Z",
    site: "site01",
    principal: "site01-controller",
    kind: "security",
    event_type: "EVENT_SCHEMA_REJECTED",
    severity: "SECURITY",
    event_id: "e-1",
    claimed_ts: null,
    details: { message },
    ...extra,
  };
}

function renderRow(event: EventRow) {
  return render(
    <table>
      <tbody>
        <EventRowView event={event} />
      </tbody>
    </table>,
  );
}

afterEach(() => cleanup());

describe("EventRowView renders untrusted details as text", () => {
  it.each([SCRIPT, IMG])("shows %s literally and creates no element", (payload) => {
    const { container } = renderRow(makeEvent(payload));
    const cell = screen.getByTestId("event-details");
    expect(cell.textContent).toBe(`message: ${payload}`);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    // The only children of the details cell are text nodes.
    expect(Array.from(cell.childNodes).every((n) => n.nodeType === Node.TEXT_NODE)).toBe(true);
  });

  it("neutralises markup in principal, event_type and nested details", () => {
    const { container } = renderRow(
      makeEvent(SCRIPT, {
        principal: IMG,
        event_type: SCRIPT,
        details: { message: IMG, nested: { html: SCRIPT }, n: 3 },
      }),
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain(IMG);
    expect(container.textContent).toContain("alert(1)");
  });

  it("renders both payloads in a full EventsTable without creating elements", () => {
    const events = [makeEvent(SCRIPT), { ...makeEvent(IMG), id: 2, event_id: "e-2" }];
    const { container } = render(<EventsTable events={events} testId="events-table" />);
    const text = screen.getByTestId("events-table").textContent ?? "";
    expect(text).toContain(SCRIPT);
    expect(text).toContain(IMG);
    expect(container.querySelectorAll("script, img, iframe, object")).toHaveLength(0);
  });

  it("truncates very long details to about 200 characters", () => {
    renderRow(makeEvent("A".repeat(5000)));
    const cell = screen.getByTestId("event-details");
    expect((cell.textContent ?? "").length).toBeLessThanOrEqual(200);
  });
});
