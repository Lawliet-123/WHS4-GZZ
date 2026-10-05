// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { aggregateLauncherState } from "./App";
import { demoEvents, demoOverview, demoSnapshots, demoStatuses } from "./mockData";

beforeEach(() => vi.useFakeTimers());

afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("dashboard interactions", () => {
  it("shows operational data without the removed marketing and disclaimer copy", () => {
    render(<App />);
    expect(screen.getByText("통합 관제")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "플레이어 판정" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "보호 모듈" })).toBeTruthy();
    expect(document.querySelectorAll(".module-overview-card")).toHaveLength(13);
    expect(screen.getAllByText("의심 근거 있음").length).toBeGreaterThan(0);
    expect(screen.queryByText("안티치트 관제 대시보드")).toBeNull();
    expect(screen.queryByText(/현재 보호 상태와 탐지 근거/)).toBeNull();
    expect(screen.queryByText(/의심 상태는 검토 우선순위/)).toBeNull();
    expect(screen.queryByText(/현재는 시연 데이터입니다/)).toBeNull();
    expect(screen.getAllByText("미제공").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("heading", { name: "GodMode 사건 이력" })).toBeTruthy();
  });

  it("keeps the sidebar selection synchronized with the visible section", () => {
    const resizeCallbacks: ResizeObserverCallback[] = [];
    class ResizeObserverMock {
      constructor(callback: ResizeObserverCallback) { resizeCallbacks.push(callback); }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", ResizeObserverMock);
    window.history.replaceState(null, "", window.location.pathname);
    render(<App />);
    const positions: Record<string, number> = {
      overview: -900,
      modules: -500,
      sessions: 40,
      subjects: 500,
      events: 900,
      systems: 1300,
    };
    const rect = (top: number) => ({
      x: 0, y: top, top, right: 100, bottom: top + 100, left: 0,
      width: 100, height: 100, toJSON: () => ({}),
    } as DOMRect);
    for (const [id, top] of Object.entries(positions)) {
      const section = document.getElementById(id);
      expect(section).toBeTruthy();
      vi.spyOn(section!, "getBoundingClientRect").mockImplementation(() => rect(positions[id] ?? top));
    }

    act(() => vi.runOnlyPendingTimers());
    expect(screen.getByRole("link", { name: "세션" }).getAttribute("aria-current")).toBe("location");

    positions.subjects = -300;
    positions.events = 30;
    act(() => {
      fireEvent.scroll(window);
      vi.runOnlyPendingTimers();
    });
    expect(screen.getByRole("link", { name: "이벤트" }).getAttribute("aria-current")).toBe("location");
    expect(screen.getByRole("link", { name: "세션" }).getAttribute("aria-current")).toBeNull();

    positions.events = -300;
    positions.systems = 30;
    act(() => {
      resizeCallbacks[0]?.([], {} as ResizeObserver);
      vi.runOnlyPendingTimers();
    });
    expect(screen.getByRole("link", { name: "시스템" }).getAttribute("aria-current")).toBe("location");
  });

  it("filters the subject list and event table by module", () => {
    render(<App />);
    fireEvent.change(screen.getByLabelText("모듈"), { target: { value: "external_access" } });
    expect(screen.getAllByText(/External process opened PROCESS_VM_WRITE handle/).length).toBeGreaterThan(0);
    expect(screen.queryByText("Collision Disabled Too Long")).toBeNull();
    expect(screen.getAllByText("player_042").length).toBeGreaterThan(0);
  });

  it("keeps heartbeat-only module state visible when its Event filter has no matches", () => {
    render(<App />);
    fireEvent.change(screen.getByLabelText("모듈"), { target: { value: "kernel_watcher" } });

    const moduleCard = screen.getByRole("button", { name: "Kernel Watcher 필터 적용" });
    expect(moduleCard.textContent).toContain("일부 저하");
    expect(moduleCard.textContent).not.toContain("미보고");
  });

  it("does not collapse transitional Launcher states into healthy", () => {
    expect(aggregateLauncherState(["starting"])).toBe("starting");
    expect(aggregateLauncherState(["healthy", "starting"])).toBe("starting");
    expect(aggregateLauncherState(["healthy", "stopped"])).toBe("stopped");
  });

  it("applies the selected scope to summary cards and sessions", () => {
    render(<App />);
    fireEvent.change(screen.getByLabelText("모듈"), { target: { value: "external_access" } });

    const summary = document.querySelector(".summary-grid");
    expect(summary).toBeTruthy();
    const summaryValue = (label: string) => within(summary as HTMLElement).getByText(label).closest(".summary-card")?.querySelector("strong")?.textContent;
    expect(summaryValue("조회 세션")).toBe("1");
    expect(summaryValue("조회 플레이어")).toBe("1");
    expect(summaryValue("기록 Event")).toBe("1");
    expect(summaryValue("운영 Event")).toBe("0");
    expect(screen.getByRole("button", { name: "demo_esp_001" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "demo_noclip_001" })).toBeNull();
  });

  it("limits the player selector to the selected session and clears an incompatible player", () => {
    render(<App />);
    const sessionSelect = screen.getByLabelText("세션") as HTMLSelectElement;
    const playerSelect = screen.getByLabelText("플레이어") as HTMLSelectElement;

    fireEvent.change(sessionSelect, { target: { value: "demo_esp_001" } });
    expect([...playerSelect.options].map((option) => option.value)).toEqual(["ALL", "player_042"]);

    fireEvent.change(playerSelect, { target: { value: "player_042" } });
    fireEvent.change(sessionSelect, { target: { value: "demo_noclip_001" } });
    expect(playerSelect.value).toBe("ALL");
    expect([...playerSelect.options].map((option) => option.value)).toEqual(["ALL", "player_013"]);
  });

  it("clears the selected subject when filters return no results", () => {
    render(<App />);
    fireEvent.change(screen.getByLabelText("검색"), { target: { value: "no-such-session-or-player" } });
    expect(screen.getByText("조건에 맞는 대상 없음")).toBeTruthy();
    expect(screen.getByText("대상을 선택하세요")).toBeTruthy();
    expect(screen.getByText("조건에 맞는 이벤트 없음")).toBeTruthy();
  });

  it("opens the actual shared-event fields in the detail drawer", () => {
    render(<App />);
    const reason = screen.getAllByText("external overlay window overlaps the game viewport")[0];
    const row = reason?.closest("tr");
    expect(row).toBeTruthy();
    fireEvent.click(row!);
    expect(screen.getByRole("dialog", { name: "이벤트 상세" })).toBeTruthy();
    expect(screen.getByText("공통 이벤트 JSON")).toBeTruthy();
    expect(screen.getByText("event_type")).toBeTruthy();
    expect(screen.queryByText("비식별 Evidence")).toBeNull();
  });

  it("never attaches a delayed previous-subject response to the current subject", async () => {
    vi.useRealTimers();
    const originalShowModal = HTMLDialogElement.prototype.showModal;
    const originalClose = HTMLDialogElement.prototype.close;
    HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute("open", ""); };
    HTMLDialogElement.prototype.close = function close() { this.removeAttribute("open"); };

    const longPlayer = "BP_FirstPersonCharacter_Hunter_Default_C_2147479755";
    let resolveOldSnapshot: ((response: Response) => void) | undefined;
    let resolveOldStatus: ((response: Response) => void) | undefined;
    let resolveOldHistory: ((response: Response) => void) | undefined;
    const oldSnapshotResponse = new Promise<Response>((resolve) => { resolveOldSnapshot = resolve; });
    const oldStatusResponse = new Promise<Response>((resolve) => { resolveOldStatus = resolve; });
    const oldHistoryResponse = new Promise<Response>((resolve) => { resolveOldHistory = resolve; });
    const response = (body: unknown): Response => ({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => body,
    } as Response);

    const fetchMock = vi.fn((request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/api/dashboard/overview")) return Promise.resolve(response(demoOverview));
      if (url.includes("/api/dashboard/events?")) return Promise.resolve(response(demoEvents));
      if (url.includes("/snapshot") && url.includes(longPlayer)) return oldSnapshotResponse;
      if (url.includes("/status") && url.includes(longPlayer)) return oldStatusResponse;
      if (url.includes("/history") && url.includes(longPlayer)) return oldHistoryResponse;
      if (url.includes("/snapshot") && url.includes("player_042")) return Promise.resolve(response(demoSnapshots["demo_esp_001::player_042"]));
      if (url.includes("/status") && url.includes("player_042")) return Promise.resolve(response(demoStatuses["demo_esp_001::player_042"]));
      if (url.includes("/history") && url.includes("player_042")) return Promise.resolve(response({ items: [], has_more: false, next_after_sequence: null, final_assessment: false }));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    try {
      render(<App />);
      fireEvent.click(screen.getByRole("button", { name: "연결" }));
      const dialog = screen.getByRole("dialog");
      fireEvent.change(within(dialog).getByLabelText("Dashboard 토큰"), { target: { value: "test-token" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "연결" }));

      await waitFor(() => expect(screen.getByText("LIVE")).toBeTruthy());
      fireEvent.click(screen.getByRole("button", { name: /player_042.*demo_esp_001/ }));
      await waitFor(() => expect(screen.getByRole("heading", { name: "player_042" })).toBeTruthy());
      await waitFor(() => {
        const moduleCards = [...document.querySelectorAll(".module-state-card")];
        expect(moduleCards.some((card) => card.textContent?.includes("ESP 접근 감시"))).toBe(true);
      });

      resolveOldSnapshot?.(response(demoSnapshots[`demo_aimbot_001::${longPlayer}`]));
      resolveOldStatus?.(response(demoStatuses[`demo_aimbot_001::${longPlayer}`]));
      resolveOldHistory?.(response({
        items: [{ event_id: "old-history", sequence: 999, session_id: "demo_aimbot_001", player_id: longPlayer, module: "godmode", timestamp_ms: 1000, raw_score: 3, evidence: {}, reasons: ["OLD SUBJECT HISTORY"] }],
        has_more: false,
        next_after_sequence: null,
        final_assessment: false,
      }));
      await Promise.resolve();
      await Promise.resolve();

      expect(screen.getByRole("heading", { name: "player_042" })).toBeTruthy();
      const moduleCards = [...document.querySelectorAll(".module-state-card")];
      expect(moduleCards.some((card) => card.textContent?.includes("입력 행동"))).toBe(false);
      expect(screen.queryByText("OLD SUBJECT HISTORY")).toBeNull();
    } finally {
      vi.unstubAllGlobals();
      HTMLDialogElement.prototype.showModal = originalShowModal;
      HTMLDialogElement.prototype.close = originalClose;
    }
  });

  it("keeps the last successful subject details and marks live data stale when refresh fails", async () => {
    vi.useRealTimers();
    const originalShowModal = HTMLDialogElement.prototype.showModal;
    const originalClose = HTMLDialogElement.prototype.close;
    HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute("open", ""); };
    HTMLDialogElement.prototype.close = function close() { this.removeAttribute("open"); };
    let failRefresh = false;
    const response = (body: unknown): Response => ({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => body,
    } as Response);
    const firstAssessment = demoOverview.assessments[0]!;
    const firstKey = `${firstAssessment.session_id}::${firstAssessment.player_id}`;
    const fetchMock = vi.fn((request: RequestInfo | URL) => {
      const url = String(request);
      if (failRefresh && (url.includes("/api/dashboard/overview") || url.includes("/api/dashboard/events?"))) {
        return Promise.reject(new Error("temporary refresh failure"));
      }
      if (url.includes("/api/dashboard/overview")) return Promise.resolve(response(demoOverview));
      if (url.includes("/api/dashboard/events?")) return Promise.resolve(response(demoEvents));
      if (url.includes("/snapshot")) return Promise.resolve(response(demoSnapshots[firstKey]));
      if (url.includes("/status")) return Promise.resolve(response(demoStatuses[firstKey]));
      if (url.includes("/history")) return Promise.resolve({ ...response({ detail: "unavailable" }), ok: false, status: 503 } as Response);
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    try {
      render(<App />);
      fireEvent.click(screen.getByRole("button", { name: "연결" }));
      const dialog = screen.getByRole("dialog");
      fireEvent.change(within(dialog).getByLabelText("Dashboard 토큰"), { target: { value: "test-token" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "연결" }));

      await waitFor(() => expect(screen.getByText("LIVE")).toBeTruthy());
      await waitFor(() => expect(document.querySelectorAll(".module-state-card").length).toBeGreaterThan(0));
      await waitFor(() => expect(screen.getByText("Dashboard 데이터 저장소를 현재 사용할 수 없습니다.")).toBeTruthy());
      const detailBeforeFailure = [...document.querySelectorAll(".module-state-card")].map((card) => card.textContent).join(" ");

      failRefresh = true;
      fireEvent.click(screen.getByRole("button", { name: "새로고침" }));
      await waitFor(() => expect(screen.getByText("LIVE · 지연")).toBeTruthy());
      const detailAfterFailure = [...document.querySelectorAll(".module-state-card")].map((card) => card.textContent).join(" ");
      expect(detailAfterFailure).toBe(detailBeforeFailure);
    } finally {
      vi.unstubAllGlobals();
      HTMLDialogElement.prototype.showModal = originalShowModal;
      HTMLDialogElement.prototype.close = originalClose;
    }
  });
});
