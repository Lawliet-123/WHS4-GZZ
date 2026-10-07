// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SelfDefenseStates, selfDefenseEvent } from "./SelfDefenseStates";
import type { DashboardEvent, SelfDefenseStatus } from "../types";

const state = (changes: Partial<SelfDefenseStatus> = {}): SelfDefenseStatus => ({
  session_id: "session_test", player_id: "player_test", kind: "file_integrity", component: "integrity",
  target_module: null, status: "ERROR", scan_complete: false, scope: "approved_release",
  timestamp_ms: 246771, sequence: 100, event_id: "operational-100", raw_score: 0,
  reasons: ["BASELINE_UNAVAILABLE"], evidence: { kind: "file_integrity", status: "ERROR" }, ...changes,
});
const original = (item: SelfDefenseStatus): DashboardEvent => ({
  ...selfDefenseEvent(item, []), time_basis: "session_relative", observed_at_utc: "2026-10-07T10:12:44.957Z",
  received_at_utc: "2026-10-07T10:12:46.123Z",
});
afterEach(cleanup);

describe("SelfDefense operational projection", () => {
  it("keeps raw-zero errors and debugger detection separate from a cheat verdict", () => {
    render(<SelfDefenseStates items={[state(), state({ kind: "debugger_presence", status: "DETECTED", event_id: "debugger-101", sequence: 101 })]} events={[]} />);
    const table = screen.getByRole("table", { name: "SelfDefense 기능별 상태" });
    expect(within(table).getByText("ERROR").className).toContain("tone-danger");
    expect(within(table).getByText("DETECTED").className).toContain("tone-danger");
    expect(within(table).getAllByText("미완료")).toHaveLength(2);
    expect(within(table).queryByText("NORMAL")).toBeNull();
    expect(within(table).queryByText("SUSPICIOUS")).toBeNull();
  });

  it("preserves independent Watchdog targets and exposes the original record", () => {
    const items = [state({ kind: "module_health", target_module: "esp" }), state({ kind: "module_health", target_module: "hide_anywhere", event_id: "watchdog-hide", sequence: 101 })];
    const onSelect = vi.fn();
    render(<SelfDefenseStates items={items} events={[]} onSelect={onSelect} />);
    expect(screen.getAllByText("Watchdog")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: /ESP 접근 감시 운영 기록 상세/ }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: items[0]!.event_id, module: "selfdefense", raw_score: 0, time_basis: "unknown" }));
  });

  it("only reuses clocks from the matching original event", () => {
    const item = state(), event = original(item);
    expect(selfDefenseEvent(item, [event])).toBe(event);
    for (const changes of [{ player_id: "other" }, { sequence: 999 }, { timestamp_ms: 999 }, { raw_score: 1 }, { module: "esp" }]) {
      expect(selfDefenseEvent(item, [{ ...event, ...changes }])).toMatchObject({ time_basis: "unknown", observed_at_utc: null, received_at_utc: null });
    }
    render(<SelfDefenseStates items={[item]} events={[event]} />);
    expect(screen.getByText("경과 04:06.771")).toBeTruthy();
    expect(screen.getByText("관측 2026-10-07 19:12:44.957 KST")).toBeTruthy();
  });

  it("does not infer time from overview evidence or mark an incomplete check healthy", () => {
    const item = state({ status: "NORMAL", evidence: { time_basis: "session_relative", session_start_unix_ms: 1791367718186 } });
    render(<SelfDefenseStates items={[item]} events={[]} issuesOnly />);
    expect(screen.getByText("NORMAL")).toBeTruthy();
    expect(screen.getByText("미완료")).toBeTruthy();
    expect(screen.getByText("관측 시각 미제공")).toBeTruthy();
    expect(screen.queryByText(/경과 /)).toBeNull();
  });

  it("distinguishes unsupported API from an empty result and filters completed healthy rows", () => {
    const view = render(<SelfDefenseStates items={undefined} events={[]} />);
    expect(screen.getByText("SelfDefense 상태 API 미제공")).toBeTruthy();
    view.rerender(<SelfDefenseStates items={[]} events={[]} />);
    expect(screen.getByText("SelfDefense 운영 기록 없음")).toBeTruthy();
    view.rerender(<SelfDefenseStates items={[state({ status: "NORMAL", scan_complete: true })]} events={[]} issuesOnly />);
    expect(screen.getByText("점검할 SelfDefense 상태 없음")).toBeTruthy();
  });

  it("renders future function names safely and redacts full paths in status evidence", () => {
    render(<SelfDefenseStates items={[state({ kind: "constructor", scope: "C:\\Users\\lab-user\\release", reasons: ["C:\\Users\\lab-user\\private.txt"] })]} events={[]} />);
    expect(screen.getByText("constructor")).toBeTruthy();
    expect(document.body.textContent).not.toContain("lab-user");
    expect(document.body.textContent).not.toContain("C:\\Users");
  });
});
