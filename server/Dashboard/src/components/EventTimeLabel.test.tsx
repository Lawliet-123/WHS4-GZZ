// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { DashboardEvent } from "../types";
import { EventTimeLabel } from "./EventTimeLabel";

const event: DashboardEvent = {
  id: "clock-example", sequence: 37, session_id: "session", player_id: "player", module: "hide_anywhere",
  timestamp_ms: 246_771, raw_score: 0, reasons: [], evidence: {}, event_kind: "operational",
  time_basis: "session_relative", observed_at_utc: "2026-10-07T10:12:44.957Z",
  received_at_utc: "2026-10-07T10:12:45.012Z", evidence_image: null, log_excerpt: null,
};
afterEach(cleanup);

describe("EventTimeLabel", () => {
  it("shows elapsed, observation and first receipt as three explicitly separate labels", () => {
    const { container } = render(<EventTimeLabel event={event} />);
    expect(screen.getByText("경과 04:06.771")).toBeTruthy();
    expect(screen.getByText("관측 2026-10-07 19:12:44.957 KST")).toBeTruthy();
    expect(screen.getByText("수신 2026-10-07 19:12:45.012 KST")).toBeTruthy();
    expect(container.querySelector(".event-time")?.getAttribute("title")).toContain("수신 2026-10-07 19:12:45.012 KST");
  });

  it("does not hide missing observation behind an available receipt", () => {
    render(<EventTimeLabel event={{ ...event, observed_at_utc: null }} />);
    expect(screen.getByText("관측 시각 미제공")).toBeTruthy();
    expect(screen.getByText("수신 2026-10-07 19:12:45.012 KST")).toBeTruthy();
    expect(screen.queryByText("관측 2026-10-07 19:12:45.012 KST")).toBeNull();
  });

  it("leaves legacy unknown raw and shows no invented receipt", () => {
    render(<EventTimeLabel event={{ ...event, time_basis: "unknown", received_at_utc: null }} />);
    expect(screen.getByText("timestamp 246771 ms")).toBeTruthy();
    expect(screen.getByText("관측 시각 미제공")).toBeTruthy();
    expect(screen.queryByText(/KST/)).toBeNull();
  });
});
