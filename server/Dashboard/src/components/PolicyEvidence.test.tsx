// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoEvents, demoSnapshots } from "../mockData";
import type { DashboardEvent, SnapshotResponse } from "../types";
import { ModuleSignals, VerdictEvidence } from "./PolicyEvidence";

afterEach(cleanup);

function fixtures() {
  const incident: DashboardEvent = { ...demoEvents.items[0]!, id: "retained", session_id: "session", player_id: "player", module: "external_access", raw_score: 2, timestamp_ms: 1000, sequence: 1, evidence: { submodule: "module_integrity" }, reasons: ["Unsigned added DLL"] };
  const latest: DashboardEvent = { ...incident, id: "latest-zero", timestamp_ms: 2000, sequence: 2, raw_score: 0, reasons: ["No new DLL change"], evidence: { submodule: "module_integrity", status: "NORMAL" } };
  const base: SnapshotResponse = { ...demoSnapshots["demo_normal_001::player_007"]!, session_id: "session", player_id: "player", modules: [], policy: {},
    final_verdict: { ...demoSnapshots["demo_normal_001::player_007"]!.final_verdict!, active_modules: ["external_access"], status: "SUSPICIOUS", active_module_count: 1, evidence_unit_count: 1 },
  };
  const snapshot: SnapshotResponse = { ...base, policy: { module_evidence: [{
    module: "external_access", submodule: "module_integrity",
    signal: { status: "ACTIVE", calibration_threshold: 2, calibration_mode: "event_threshold" },
    latest_event: { ...latest, event_id: latest.id }, retained_incident_event: { ...incident, event_id: incident.id },
  }] } };
  return { snapshot, incident, latest };
}

describe("VerdictEvidence", () => {
  it("shows latest zero and retained positive with server threshold and reason", () => {
    const { snapshot, incident, latest } = fixtures();
    render(<VerdictEvidence snapshot={snapshot} events={[incident, latest]} />);
    expect(screen.getByText("현재 활성 판정 근거")).toBeTruthy();
    expect(screen.getByText("세션 보존 사건")).toBeTruthy();
    expect(screen.getByText("raw ≥ 2")).toBeTruthy();
    expect(screen.getByText("사건 기준")).toBeTruthy();
    expect(screen.getByText("Unsigned added DLL")).toBeTruthy();
    expect(screen.getByText("raw 0")).toBeTruthy();
    expect(screen.getByText("raw 2")).toBeTruthy();
  });

  it("opens the correct retained incident instead of the later zero snapshot", () => {
    const { snapshot, incident, latest } = fixtures(), onSelect = vi.fn();
    render(<VerdictEvidence snapshot={snapshot} events={[incident, latest]} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /세션 보존 사건 상세/ }));
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: "retained", raw_score: 2 }));
    fireEvent.click(screen.getByRole("button", { name: /최신 관측 상세/ }));
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: "latest-zero", raw_score: 0 }));
  });

  it("keeps null threshold unprovided and omits player score/confidence", () => {
    const { snapshot, incident, latest } = fixtures();
    (snapshot.policy.module_evidence as { signal: { calibration_threshold: number | null } }[])[0]!.signal.calibration_threshold = null;
    render(<VerdictEvidence snapshot={snapshot} events={[incident, latest]} />);
    expect(screen.getByText("미제공")).toBeTruthy();
    expect(screen.queryByText(/점수|신뢰도/)).toBeNull();
    expect(screen.queryByText("raw ≥ 0")).toBeNull();
  });

  it("does not promote an uncalibrated positive observation to current active evidence", () => {
    const { snapshot, incident } = fixtures();
    snapshot.policy = {};
    snapshot.final_verdict = null;
    render(<VerdictEvidence snapshot={snapshot} events={[incident]} />);
    expect(screen.getByText("판정 근거 미제공")).toBeTruthy();
    expect(screen.queryByText("현재 활성")).toBeNull();
    expect(screen.getByText("상태 미제공")).toBeTruthy();
  });

  it("does not present policy ACTIVE as final-verdict evidence when the provider is missing", () => {
    const { snapshot, incident, latest } = fixtures();
    snapshot.status = "UNKNOWN";
    snapshot.final_verdict = null;
    snapshot.assessment_available = false;
    render(<VerdictEvidence snapshot={snapshot} events={[incident, latest]} />);
    expect(screen.getByText("판정 근거 미제공")).toBeTruthy();
    expect(screen.queryByText("현재 활성")).toBeNull();
    expect(screen.queryByText("Unsigned added DLL")).toBeNull();
    expect(screen.queryByText("raw ≥ 2")).toBeNull();
  });

  it("does not attach policy ACTIVE from a different read generation to NO_ACTIVE_EVIDENCE", () => {
    const { snapshot, incident, latest } = fixtures();
    snapshot.status = "NO_ACTIVE_EVIDENCE";
    snapshot.final_verdict = { ...snapshot.final_verdict!, status: "NO_ACTIVE_EVIDENCE", active_modules: [], active_module_count: 0, evidence_unit_count: 0 };
    render(<VerdictEvidence snapshot={snapshot} events={[incident, latest]} />);
    expect(screen.getByText("현재 활성 근거 없음")).toBeTruthy();
    expect(screen.queryByText("현재 활성")).toBeNull();
    expect(screen.queryByText("Unsigned added DLL")).toBeNull();
    expect(screen.queryByText("raw ≥ 2")).toBeNull();
  });

  it("does not label unknown stored timestamps as session elapsed time", () => {
    const { snapshot, incident, latest } = fixtures();
    const entries = snapshot.policy.module_evidence as { latest_event: DashboardEvent; retained_incident_event: DashboardEvent }[];
    entries[0]!.latest_event.time_basis = "unknown";
    entries[0]!.retained_incident_event.time_basis = "unknown";
    render(<VerdictEvidence snapshot={snapshot} events={[{ ...incident, time_basis: "unknown" }, { ...latest, time_basis: "unknown" }]} />);
    expect(screen.getByText("timestamp 1000 ms")).toBeTruthy();
    expect(screen.getByText("timestamp 2000 ms")).toBeTruthy();
    expect(screen.queryByText(/경과 /)).toBeNull();
  });

  it("uses the same original event's explicit clocks for a stored-state witness", () => {
    const { snapshot, incident, latest } = fixtures();
    const entries = snapshot.policy.module_evidence as { latest_event: DashboardEvent; retained_incident_event: DashboardEvent }[];
    entries[0]!.latest_event.time_basis = "unknown";
    entries[0]!.latest_event.received_at_utc = null;
    entries[0]!.latest_event.observed_at_utc = null;
    const knownOriginal: DashboardEvent = { ...latest, time_basis: "session_relative", observed_at_utc: "2026-10-06T15:00:00Z", received_at_utc: "2026-10-06T15:00:01Z" };
    render(<VerdictEvidence snapshot={snapshot} events={[incident, knownOriginal]} />);
    expect(screen.getByText("경과 00:02.000")).toBeTruthy();
    expect(screen.getByText("관측 2026-10-07 00:00:00.000 KST")).toBeTruthy();
    expect(screen.getByText("수신 2026-10-07 00:00:01.000 KST")).toBeTruthy();
  });

  it("does not borrow clock metadata from a different event with the same timestamp", () => {
    const { snapshot, incident, latest } = fixtures();
    const entries = snapshot.policy.module_evidence as { latest_event: DashboardEvent; retained_incident_event: DashboardEvent }[];
    entries[0]!.latest_event.time_basis = "unknown";
    entries[0]!.latest_event.received_at_utc = null;
    entries[0]!.latest_event.observed_at_utc = null;
    const otherEvent: DashboardEvent = { ...latest, id: "different-source", time_basis: "session_relative", observed_at_utc: "2026-10-06T15:00:00Z" };
    render(<VerdictEvidence snapshot={snapshot} events={[incident, otherEvent]} />);
    expect(screen.getByText("timestamp 2000 ms")).toBeTruthy();
    expect(screen.queryByText("관측 2026-10-07 00:00:00.000 KST")).toBeNull();
  });
});

describe("ModuleSignals", () => {
  it("sorts latest and loaded historical scores while keeping state INACTIVE", () => {
    const { snapshot, incident, latest } = fixtures();
    const highPast = { ...incident, id: "past-aimbot", module: "aimbot", raw_score: 9, evidence: {} };
    const aimbotLatest = { ...latest, id: "current-aimbot", module: "aimbot", raw_score: 1, evidence: {} };
    (snapshot.policy.module_evidence as unknown[]).push({ module: "aimbot", submodule: null, signal: { status: "INACTIVE", calibration_threshold: 4 }, latest_event: { ...aimbotLatest, event_id: aimbotLatest.id }, retained_incident_event: null });
    const { container } = render(<ModuleSignals snapshot={snapshot} events={[incident, latest, highPast, aimbotLatest]} />);
    let rows = container.querySelectorAll("tbody tr");
    expect(rows[0]!.textContent).toContain("입력 행동");
    fireEvent.change(screen.getByRole("combobox", { name: "모듈 신호 정렬" }), { target: { value: "highest" } });
    rows = container.querySelectorAll("tbody tr");
    expect(rows[0]!.textContent).toContain("입력 행동");
    expect(within(rows[0] as HTMLElement).getByText("현재 비활성")).toBeTruthy();
    expect(screen.getByText("불러온 이력 최고 raw")).toBeTruthy();
  });

  it("shows no data for a missing snapshot instead of manufacturing zero-valued signals", () => {
    render(<ModuleSignals snapshot={null} events={[]} />);
    expect(screen.getByText("모듈 신호 없음")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
