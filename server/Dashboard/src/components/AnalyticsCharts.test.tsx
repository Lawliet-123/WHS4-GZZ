// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoEvents, demoOverview } from "../mockData";
import type { Assessment, DashboardEvent, VerdictStatus } from "../types";
import { OverviewAnalytics, SessionObservationChart } from "./AnalyticsCharts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function event(sequence: number, changes: Partial<DashboardEvent> = {}): DashboardEvent {
  return {
    ...demoEvents.items[0]!,
    id: `chart-event-${sequence}`,
    sequence,
    session_id: "session_a",
    player_id: "chart_player",
    evidence: { synthetic: true },
    reasons: [],
    ...changes,
  };
}

const unknownAssessment: Assessment = {
  ...demoOverview.assessments[0]!,
  id: "chart-unknown-assessment",
  session_id: "session_unknown",
  player_id: "chart_unknown_player",
  status: "UNKNOWN",
  assessment_available: false,
  final_verdict: null,
  data_state: "missing",
  reason_codes: [],
};

// Keep all four rendering states explicit rather than depending on whichever
// calibrated verdict the evolving DEMO scenario happens to produce.
const allStatusAssessments: Assessment[] = demoOverview.assessments.map((assessment) => (
  assessment.session_id === "demo_esp_001"
    ? {
      ...assessment,
      status: "INCONCLUSIVE",
      reason_codes: ["ASSESSMENT_INCOMPLETE"],
      final_verdict: {
        ...assessment.final_verdict!,
        status: "INCONCLUSIVE",
        assessment_complete: false,
        evidence_unit_count: 0,
        active_module_count: 0,
        active_modules: [],
        unresolved_modules: ["esp"],
        reason_codes: ["ASSESSMENT_INCOMPLETE"],
      },
    }
    : assessment
));

describe("OverviewAnalytics", () => {
  it("renders an empty donut without invalid geometry and disables empty verdicts", () => {
    const onVerdict = vi.fn();
    const onDetector = vi.fn();
    const { container } = render(
      <OverviewAnalytics assessments={[]} events={[]} onVerdict={onVerdict} onDetector={onDetector} />,
    );

    expect(screen.getByRole("img", { name: "판정 데이터 없음" })).toBeTruthy();
    expect(screen.getByText("0 대상")).toBeTruthy();
    expect(screen.getByText("관측 데이터 없음")).toBeTruthy();
    expect(container.querySelectorAll(".ring-segment")).toHaveLength(0);
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
    const verdictButtons = screen.getAllByRole("button");
    expect(verdictButtons).toHaveLength(4);
    for (const button of verdictButtons) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
      fireEvent.click(button);
    }
    expect(onVerdict).not.toHaveBeenCalled();
    expect(onDetector).not.toHaveBeenCalled();
  });

  it("shows all four statuses once per assessment pair with finite donut totals", () => {
    const assessments = [...allStatusAssessments, unknownAssessment, { ...unknownAssessment }];
    const { container } = render(
      <OverviewAnalytics assessments={assessments} events={demoEvents.items} onVerdict={vi.fn()} onDetector={vi.fn()} />,
    );

    expect(screen.getByText("5 대상")).toBeTruthy();
    expect(screen.getByRole("img", {
      name: "세션·플레이어 판정: SUSPICIOUS 2대상, INCONCLUSIVE 1대상, NO_ACTIVE_EVIDENCE 1대상, UNKNOWN 1대상",
    })).toBeTruthy();
    const segments = [...container.querySelectorAll(".ring-segment")];
    expect(segments).toHaveLength(4);
    const lengths = segments.map((segment) => {
      const values = segment.getAttribute("stroke-dasharray")!.split(" ").map(Number);
      expect(values.every(Number.isFinite)).toBe(true);
      expect(Number.isFinite(Number(segment.getAttribute("stroke-dashoffset")))).toBe(true);
      return values[0]!;
    });
    expect(lengths.reduce((sum, value) => sum + value, 0)).toBeCloseTo(2 * Math.PI * 57);
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
  });

  it("passes the exact authoritative status when each verdict legend is clicked", () => {
    const onVerdict = vi.fn();
    render(
      <OverviewAnalytics assessments={[...allStatusAssessments, unknownAssessment]} events={[]} onVerdict={onVerdict} onDetector={vi.fn()} />,
    );

    const counts: Array<[VerdictStatus, number]> = [
      ["SUSPICIOUS", 2], ["INCONCLUSIVE", 1], ["NO_ACTIVE_EVIDENCE", 1], ["UNKNOWN", 1],
    ];
    for (const [status, count] of counts) {
      fireEvent.click(screen.getByRole("button", { name: `${status} ${count}대상 보기` }));
    }
    expect(onVerdict.mock.calls).toEqual(counts.map(([status]) => [status]));
  });

  it("retains zero-point observations, excludes operating events, and drills into the logical DLL detector", () => {
    const onDetector = vi.fn();
    const events = [
      event(1, { module: "external_access", raw_score: 0, evidence: { submodule: "module_integrity" } }),
      event(2, { module: "external_access", raw_score: 7 }),
      event(3, { module: "external_access", raw_score: 999, event_kind: "operational", evidence: { submodule: "module_integrity" } }),
      event(4, { module: "selfdefense", raw_score: 999, event_kind: "operational" }),
    ];
    render(<OverviewAnalytics assessments={[]} events={events} onVerdict={vi.fn()} onDetector={onDetector} />);

    expect(screen.getByText("불러온 2건")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "게임 모듈 무결성 관측 1건 보기" }));
    fireEvent.click(screen.getByRole("button", { name: "외부 프로세스 접근 관측 1건 보기" }));
    expect(onDetector.mock.calls).toEqual([["module_integrity"], ["external_access"]]);
    expect(screen.queryByRole("button", { name: /Self Defense 관측/ })).toBeNull();
    expect(screen.getByText("0점 포함 · 관측 건수")).toBeTruthy();
  });

  it("renders long unknown labels literally without inserting their markup", () => {
    const rawModule = `unknown_${"long_label_".repeat(30)}<img src=x onerror=alert(1)>`;
    const onDetector = vi.fn();
    const { container } = render(
      <OverviewAnalytics assessments={[]} events={[event(1, { module: rawModule })]} onVerdict={vi.fn()} onDetector={onDetector} />,
    );

    const label = `미등록 · ${rawModule.replaceAll("_", " ")}`;
    const button = screen.getByRole("button", { name: `${label} 관측 1건 보기` });
    expect(button.textContent).toContain(label);
    expect(container.querySelector("img, script")).toBeNull();
    fireEvent.click(button);
    expect(onDetector).toHaveBeenCalledExactlyOnceWith(`unknown:${rawModule}`);
  });
});

describe("SessionObservationChart", () => {
  const sessionEvents = [
    event(1, { timestamp_ms: 9_000_000, raw_score: 0 }),
    event(2, { session_id: "session_b", timestamp_ms: 1, raw_score: 2 }),
    event(3, { timestamp_ms: 5, raw_score: 1 }),
    event(4, { timestamp_ms: 100, raw_score: 999, event_kind: "operational" }),
    event(5, { session_id: "session_b", timestamp_ms: 999_999, raw_score: 0 }),
    event(99, { session_id: "outside_scope", timestamp_ms: 0, raw_score: 99 }),
  ];

  it("uses allowed-session receiver sequences, not timestamps or operating events", () => {
    render(<SessionObservationChart events={sessionEvents} sessionIds={["session_a", "session_b"]} onEvents={vi.fn()} />);

    const select = screen.getByRole("combobox", { name: "그래프 세션" }) as HTMLSelectElement;
    expect(select.value).toBe("session_b");
    expect(screen.getByText("불러온 2건")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("수신 #2–#5");

    fireEvent.change(select, { target: { value: "session_a" } });
    const chart = screen.getByRole("group", { name: "session_a 서버 수신 순서별 관측 건수" });
    expect(within(chart).getAllByRole("img").map((bin) => bin.getAttribute("aria-label"))).toEqual([
      "#1 · 1건 · 양수 0건", "#2 · 0건 · 양수 0건", "#3 · 1건 · 양수 1건",
    ]);
    expect(screen.getByRole("status").textContent).toBe("수신 #1–#3");
    expect(screen.getByText("불러온 2건")).toBeTruthy();
    expect(screen.queryByRole("img", { name: /#4|#99/ })).toBeNull();
  });

  it("clears a hovered bin when selecting a new session and passes that session to drilldown", () => {
    const onEvents = vi.fn();
    render(<SessionObservationChart events={sessionEvents} sessionIds={["session_a", "session_b"]} onEvents={onEvents} />);
    const select = screen.getByRole("combobox", { name: "그래프 세션" });
    fireEvent.change(select, { target: { value: "session_a" } });
    fireEvent.mouseEnter(screen.getByRole("img", { name: "#3 · 1건 · 양수 1건" }));
    expect(screen.getByRole("status").textContent).toBe("#3–3 · 1건 · 양수 1건");

    fireEvent.change(select, { target: { value: "session_b" } });
    expect(screen.getByRole("status").textContent).toBe("수신 #2–#5");
    expect(screen.queryByRole("group", { name: "session_a 서버 수신 순서별 관측 건수" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "이 세션 이벤트 →" }));
    expect(onEvents).toHaveBeenCalledExactlyOnceWith("session_b");
  });

  it("shows an empty state for a known session with no detection events", () => {
    const { container } = render(
      <SessionObservationChart events={[event(1, { event_kind: "operational", raw_score: 99 })]} sessionIds={["session_a"]} onEvents={vi.fn()} />,
    );
    expect(screen.getByText("이 세션의 관측 데이터 없음")).toBeTruthy();
    expect(screen.queryByRole("group")).toBeNull();
    expect(screen.queryByRole("button", { name: "이 세션 이벤트 →" })).toBeNull();
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
  });

  it("disables selection when no sessions are available", () => {
    render(<SessionObservationChart events={[]} sessionIds={[]} onEvents={vi.fn()} />);
    expect((screen.getByRole("combobox", { name: "그래프 세션" }) as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getByRole("option", { name: "세션 없음" })).toBeTruthy();
    expect(screen.getByText("이 세션의 관측 데이터 없음")).toBeTruthy();
  });

  it("does not select a session just because its newest event is operational", () => {
    render(<SessionObservationChart events={[
      event(1), event(99, { session_id: "operational_only", event_kind: "operational" }),
    ]} sessionIds={["session_a", "operational_only"]} onEvents={vi.fn()} />);
    expect((screen.getByRole("combobox", { name: "그래프 세션" }) as HTMLSelectElement).value).toBe("session_a");
    expect(screen.getByText("불러온 1건")).toBeTruthy();
  });

  it("does not retain another session's hovered bin when query scope changes", () => {
    const { rerender } = render(<SessionObservationChart events={sessionEvents} sessionIds={["session_a"]} onEvents={vi.fn()} />);
    fireEvent.focus(screen.getByRole("img", { name: "#3 · 1건 · 양수 1건" }));
    expect(screen.getByRole("status").textContent).toBe("#3–3 · 1건 · 양수 1건");
    rerender(<SessionObservationChart events={sessionEvents} sessionIds={["session_b"]} onEvents={vi.fn()} />);
    expect(screen.getByRole("status").textContent).toBe("수신 #2–#5");
  });
});
