// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { aggregateLauncherState } from "./App";
import { demoEvents, demoOverview, demoSnapshots, demoStatuses } from "./mockData";
import { dashboardPageHref, type DashboardPage } from "./navigation";
import type { DashboardEvent, GodModeHistoryResponse } from "./types";

beforeEach(() => {
  vi.useFakeTimers();
  window.history.replaceState(null, "", window.location.pathname);
});

function visitPage(page: DashboardPage) {
  act(() => {
    window.history.pushState(null, "", dashboardPageHref(page));
    fireEvent(window, new HashChangeEvent("hashchange"));
  });
}

function renderPage(page: DashboardPage) {
  window.history.replaceState(null, "", dashboardPageHref(page));
  return render(<App />);
}

function withDemoEvent(sequence: number, changes: Partial<DashboardEvent>, verify: () => void) {
  const item = demoEvents.items.find((event) => event.sequence === sequence)!;
  const saved = { ...item };
  try {
    Object.assign(item, changes);
    verify();
  } finally {
    cleanup();
    Object.assign(item, saved);
  }
}

function eventRow(sequence: number): HTMLElement {
  return screen.getByRole("button", { name: new RegExp(`이벤트 #${sequence} 상세 보기$`) }).closest("tr")!;
}

function showAdvancedFilters() {
  const toggle = screen.getByRole("button", { name: /^상세 필터/ });
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
}

function useCompactViewport() {
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: true,
    media: "(max-width: 820px)",
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
}

afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function historyFixtures(count = 1) {
  const subject = demoOverview.assessments[0]!;
  const originals = Array.from({ length: count }, (_, index): DashboardEvent => ({
    ...demoEvents.items[0]!, id: `history-original-${index}`, sequence: 1000 + index,
    session_id: subject.session_id, player_id: subject.player_id, module: "godmode",
    timestamp_ms: 246771, raw_score: 3, event_kind: "detection",
    evidence: { timestamp_basis: "launcher_session_start", time_basis: "session_relative", session_start_unix_ms: 1791367718186 },
    reasons: [`History-only event ${index}`], time_basis: "session_relative",
    observed_at_utc: "2026-10-07T10:12:44.957Z", received_at_utc: "2026-10-07T10:12:46.123Z",
    evidence_image: null, log_excerpt: null,
  }));
  const history: GodModeHistoryResponse = {
    items: originals.map(({ id, sequence, session_id, player_id, timestamp_ms, raw_score, evidence, reasons }) => ({
      event_id: id, sequence, session_id, player_id, module: "godmode", timestamp_ms, raw_score, evidence, reasons,
    })), has_more: false, next_after_sequence: null, final_assessment: false,
  };
  return { subject, originals, history };
}

function mockLiveHistory(history: GodModeHistoryResponse, detail: (id: string) => unknown) {
  vi.useRealTimers();
  const originalShowModal = HTMLDialogElement.prototype.showModal;
  const originalClose = HTMLDialogElement.prototype.close;
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute("open"); };
  const response = (body: unknown): Response => ({ ok: true, status: 200, headers: new Headers({ "content-type": "application/json" }), json: async () => body } as Response);
  const subject = demoOverview.assessments[0]!;
  const key = `${subject.session_id}::${subject.player_id}`;
  const fetchMock = vi.fn((request: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(request);
    if (url.includes("/api/dashboard/overview")) return Promise.resolve(response(demoOverview));
    if (url.includes("/api/dashboard/events?")) return Promise.resolve(response(demoEvents));
    if (url.includes("/snapshot")) return Promise.resolve(response(demoSnapshots[key]));
    if (url.includes("/status")) return Promise.resolve(response(demoStatuses[key]));
    if (url.includes("/history?")) return Promise.resolve(response(history));
    if (url.includes("/api/dashboard/events/")) return Promise.resolve(response(detail(decodeURIComponent(url.split("/").at(-1)!))));
    return Promise.reject(new Error(`Unexpected request: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, restore: () => {
    HTMLDialogElement.prototype.showModal = originalShowModal;
    HTMLDialogElement.prototype.close = originalClose;
  } };
}

async function connectLiveHistory() {
  renderPage("players");
  fireEvent.click(screen.getByRole("button", { name: "연결" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Dashboard 토큰"), { target: { value: "test-token" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "연결" }));
  await waitFor(() => expect(screen.getByText("LIVE")).toBeTruthy());
  fireEvent.click(screen.getByRole("tab", { name: "GodMode 이력" }));
}

describe("dashboard interactions", () => {
  it("keeps the frontend-only build on local data without connection or token controls", () => {
    vi.stubEnv("MODE", "frontend");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    expect(screen.getByRole("heading", { name: "종합 현황" })).toBeTruthy();
    expect(screen.getByText("DEMO")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "연결" })).toBeNull();
    expect(screen.queryByLabelText("Dashboard 토큰")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    act(() => vi.advanceTimersByTime(10_000));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("allows frontend-only refresh and Event investigation without a backend request", () => {
    vi.stubEnv("MODE", "frontend");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "새로고침" }));
    fireEvent.click(screen.getByRole("button", { name: /player_042 .* 이벤트 #6 상세 보기/ }));
    const detail = screen.getByRole("dialog", { name: "이벤트 상세" });
    expect(within(detail).getByText("공통 이벤트 JSON")).toBeTruthy();
    expect(within(detail).getByText("SUSPICIOUS")).toBeTruthy();
    fireEvent.click(within(detail).getByRole("button", { name: "플레이어 판정 확인" }));
    expect(window.location.hash).toBe("#/players");
    expect(screen.getByRole("heading", { name: "player_042" })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("button", { name: "연결" })).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows operational data without the removed marketing and disclaimer copy", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "종합 현황" })).toBeTruthy();
    expect(screen.getAllByText("SUSPICIOUS").length).toBeGreaterThan(0);
    expect(screen.queryByText("안티치트 관제 대시보드")).toBeNull();
    expect(screen.queryByText(/현재 보호 상태와 탐지 근거/)).toBeNull();
    expect(screen.queryByText(/의심 상태는 검토 우선순위/)).toBeNull();
    expect(screen.queryByText(/현재는 시연 데이터입니다/)).toBeNull();
    expect(screen.queryByLabelText("검색")).toBeNull();
    expect(document.querySelectorAll(".module-entry")).toHaveLength(0);
    expect(document.querySelector(".workspace-panel")).toBeNull();
    expect(screen.queryByRole("heading", { name: "GodMode 사건 이력" })).toBeNull();
  });

  it("mounts only the active page and follows URL changes instead of scroll position", () => {
    render(<App />);
    expect(screen.getByRole("link", { name: "종합 현황" }).getAttribute("aria-current")).toBe("page");
    visitPage("modules");
    expect(screen.getByRole("link", { name: "보호 모듈" }).getAttribute("aria-current")).toBe("page");
    expect(document.querySelectorAll(".module-entry")).toHaveLength(13);
    expect(document.querySelector(".summary-grid")).toBeNull();
    visitPage("events");
    expect(screen.getByRole("link", { name: "이벤트" }).getAttribute("aria-current")).toBe("page");
    expect(document.querySelectorAll(".module-entry")).toHaveLength(0);
    expect(document.querySelector(".events-panel")).toBeTruthy();
    fireEvent.scroll(window);
    expect(screen.getByRole("link", { name: "이벤트" }).getAttribute("aria-current")).toBe("page");
    // A browser history traversal delivers the same hashchange transition.
    visitPage("modules");
    expect(screen.getByRole("link", { name: "보호 모듈" }).getAttribute("aria-current")).toBe("page");
    visitPage("system");
    expect(screen.getByRole("link", { name: "시스템" }).getAttribute("aria-current")).toBe("page");
    expect(document.querySelector(".events-panel")).toBeNull();
  });

  it("restores the active page when the browser goes back and forward", async () => {
    vi.useRealTimers();
    render(<App />);
    visitPage("sessions");
    visitPage("events");
    window.history.back();
    await waitFor(() => expect(screen.getByRole("link", { name: "세션" }).getAttribute("aria-current")).toBe("page"));
    expect(document.querySelector(".events-panel")).toBeNull();
    window.history.forward();
    await waitFor(() => expect(screen.getByRole("link", { name: "이벤트" }).getAttribute("aria-current")).toBe("page"));
    expect(document.querySelector(".events-panel")).toBeTruthy();
  });

  it("uses page URLs for every sidebar destination", () => {
    render(<App />);
    for (const [page, label] of [
      ["sessions", "세션"], ["players", "플레이어"], ["events", "이벤트"],
      ["modules", "보호 모듈"], ["system", "시스템"], ["overview", "종합 현황"],
    ] as const) {
      const link = screen.getByRole("link", { name: label });
      expect(link.getAttribute("href")).toBe(dashboardPageHref(page));
      fireEvent.click(link);
      expect(window.location.hash).toBe(dashboardPageHref(page));
      expect(link.getAttribute("aria-current")).toBe("page");
    }
  });

  it("traps mobile menu focus, closes on Escape and restores focus and scrolling", () => {
    useCompactViewport();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "auto";
    try {
      render(<App />);
      const sidebar = document.getElementById("dashboard-sidebar")!;
      const main = document.querySelector(".main-shell")!;
      const toggle = screen.getByRole("button", { name: "메뉴 열기" });
      expect(sidebar.hasAttribute("inert")).toBe(true);
      expect(sidebar.getAttribute("aria-hidden")).toBe("true");
      expect(screen.queryByRole("navigation", { name: "대시보드 메뉴" })).toBeNull();
      expect(main.hasAttribute("inert")).toBe(false);
      toggle.focus();
      fireEvent.click(toggle);
      const dialog = screen.getByRole("dialog", { name: "탐색 메뉴" });
      expect(dialog.getAttribute("aria-modal")).toBe("true");
      expect(dialog.hasAttribute("inert")).toBe(false);
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
      expect(main.hasAttribute("inert")).toBe(true);
      expect(main.getAttribute("aria-hidden")).toBe("true");
      expect(screen.queryByRole("heading", { name: "종합 현황" })).toBeNull();
      expect(document.body.style.overflow).toBe("hidden");
      const first = within(dialog).getByRole("link", { name: "종합 현황" });
      const last = within(dialog).getByRole("link", { name: "시스템" });
      expect(document.activeElement).toBe(first);
      fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
      expect(document.activeElement).toBe(last);
      fireEvent.keyDown(last, { key: "Tab" });
      expect(document.activeElement).toBe(first);
      fireEvent.keyDown(first, { key: "Escape" });
      expect(screen.queryByRole("dialog", { name: "탐색 메뉴" })).toBeNull();
      expect(sidebar.hasAttribute("inert")).toBe(true);
      expect(toggle.getAttribute("aria-expanded")).toBe("false");
      expect(main.hasAttribute("inert")).toBe(false);
      expect(main.getAttribute("aria-hidden")).toBeNull();
      expect(screen.getByRole("heading", { name: "종합 현황" })).toBeTruthy();
      expect(document.activeElement).toBe(toggle);
      expect(document.body.style.overflow).toBe("auto");
    } finally {
      cleanup();
      document.body.style.overflow = previousOverflow;
    }
  });

  it("closes the mobile menu after navigating and releases the body lock when unmounted", () => {
    useCompactViewport();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "scroll";
    try {
      const view = render(<App />);
      const toggle = screen.getByRole("button", { name: "메뉴 열기" });
      toggle.focus();
      fireEvent.click(toggle);
      fireEvent.click(within(screen.getByRole("dialog", { name: "탐색 메뉴" })).getByRole("link", { name: "이벤트" }));
      expect(window.location.hash).toBe("#/events");
      expect(screen.getByRole("heading", { name: "이벤트" })).toBeTruthy();
      expect(screen.queryByRole("dialog", { name: "탐색 메뉴" })).toBeNull();
      expect(document.getElementById("dashboard-sidebar")?.hasAttribute("inert")).toBe(true);
      expect(document.body.style.overflow).toBe("scroll");
      fireEvent.click(toggle);
      expect(document.body.style.overflow).toBe("hidden");
      view.unmount();
      expect(document.body.style.overflow).toBe("scroll");
    } finally {
      cleanup();
      document.body.style.overflow = previousOverflow;
    }
  });

  it("keeps filters scoped to the data pages and applies module filters to events", () => {
    renderPage("events");
    showAdvancedFilters();
    fireEvent.change(screen.getByLabelText("모듈"), { target: { value: "external_access" } });
    expect(screen.getAllByText(/External process opened PROCESS_VM_WRITE handle/).length).toBeGreaterThan(0);
    expect(screen.queryByText("Collision Disabled Too Long")).toBeNull();
    expect(screen.getAllByText("player_042").length).toBeGreaterThan(0);
    visitPage("overview");
    expect(screen.queryByLabelText("모듈")).toBeNull();
    expect(screen.queryByLabelText("검색")).toBeNull();
    visitPage("events");
    showAdvancedFilters();
    expect((screen.getByLabelText("모듈") as HTMLSelectElement).value).toBe("external_access");
  });

  it("keeps heartbeat-only module state visible when its Event filter has no matches", () => {
    renderPage("events");
    showAdvancedFilters();
    fireEvent.change(screen.getByLabelText("모듈"), { target: { value: "kernel_watcher" } });
    expect(screen.getByText("조건에 맞는 이벤트 없음")).toBeTruthy();
    visitPage("modules");
    const moduleCard = screen.getByRole("button", { name: "Kernel Watcher 이벤트 보기" }).closest(".module-entry")!;
    expect(moduleCard.textContent).toContain("일부 저하");
    expect(moduleCard.textContent).not.toContain("미보고");
  });

  it("does not collapse transitional Launcher states into healthy", () => {
    expect(aggregateLauncherState(["starting"])).toBe("starting");
    expect(aggregateLauncherState(["healthy", "starting"])).toBe("starting");
    expect(aggregateLauncherState(["healthy", "stopped"])).toBe("stopped");
  });

  it("applies the selected evidence scope to the session page", () => {
    renderPage("sessions");
    showAdvancedFilters();
    fireEvent.change(screen.getByLabelText("모듈"), { target: { value: "external_access" } });
    expect(screen.getByRole("button", { name: "demo_esp_001" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "demo_noclip_001" })).toBeNull();
  });

  it("limits the player selector to the selected session and clears an incompatible player", () => {
    renderPage("events");
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
    renderPage("players");
    fireEvent.change(screen.getByLabelText("검색"), { target: { value: "no-such-session-or-player" } });
    expect(screen.getByText("조건에 맞는 대상 없음")).toBeTruthy();
    expect(screen.getByText("대상을 선택하세요")).toBeTruthy();
    visitPage("events");
    expect(screen.getByText("조건에 맞는 이벤트 없음")).toBeTruthy();
  });

  it("opens the actual shared-event fields in the detail drawer", () => {
    renderPage("events");
    const reason = screen.getAllByText("external overlay window overlaps the game viewport")[0];
    const row = reason?.closest("tr");
    expect(row).toBeTruthy();
    fireEvent.click(row!);
    expect(screen.getByRole("dialog", { name: "이벤트 상세" })).toBeTruthy();
    expect(screen.getByText("공통 이벤트 JSON")).toBeTruthy();
    expect(screen.getByText("event_type")).toBeTruthy();
    expect(screen.queryByText("비식별 Evidence")).toBeNull();
  });

  it("shows observation and authoritative verdict metrics without inventing Critical or client counts", () => {
    render(<App />);
    const summary = screen.getByLabelText("요약");
    expect(summary.querySelectorAll(".summary-card")).toHaveLength(4);
    const value = (label: string) => within(summary).getByText(label).closest(".summary-card")?.querySelector("strong")?.textContent;
    expect(value("탐지 기록")).toBe(String(demoEvents.items.filter((event) => event.event_kind === "detection").length));
    expect(within(summary).queryByText("Critical")).toBeNull();
    expect(value("의심 판정")).toBe(String(demoOverview.assessments.filter((item) => item.status === "SUSPICIOUS").length));
    const clients = new Set(demoOverview.launcher_statuses
      .filter((status) => status.connected && ["healthy", "online"].includes(status.state))
      .map((status) => status.source?.client_id).filter(Boolean));
    expect(value("연결 클라이언트")).toBe(String(clients.size));
    const recent = screen.getByRole("table", { name: "최근 탐지 기록" });
    const rows = within(recent).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(8);
    for (const row of rows) {
      const sequence = Number(row.querySelector("td > small.mono")?.textContent?.replace("#", ""));
      expect(demoEvents.items.find((event) => event.sequence === sequence)?.event_kind).toBe("detection");
    }
    expect(within(recent).queryByText("운영")).toBeNull();
  });

  it("drills down from the verdict graph without mixing risk severity and Final Verdict", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "SUSPICIOUS 3대상 보기" }));
    expect(window.location.hash).toBe("#/players");
    showAdvancedFilters();
    expect((screen.getByRole("combobox", { name: /^판정$/ }) as HTMLSelectElement).value).toBe("SUSPICIOUS");
    expect(document.querySelectorAll(".subject-list > button")).toHaveLength(3);
  });

  it("drills down from detector count bars to only that detector's observations", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "ESP 관측 1건 보기" }));
    expect(window.location.hash).toBe("#/events");
    showAdvancedFilters();
    expect((screen.getByLabelText("모듈") as HTMLSelectElement).value).toBe("esp");
    const rows = within(screen.getByRole("table", { name: "전체 이벤트" })).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.textContent).toContain("ESP");
  });

  it("shows event-specific states and hides Severity when no event supplies one", () => {
    renderPage("events");
    const table = screen.getByRole("table", { name: "전체 이벤트" });
    for (const name of ["시간", "플레이어 / 클라이언트", "탐지기", "탐지 유형 / 근거", "Raw 점수", "이벤트 상태", "상세"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeTruthy();
    }
    expect(within(table).queryByRole("columnheader", { name: "Severity" })).toBeNull();
    expect(within(table).queryByRole("columnheader", { name: "대상 판정" })).toBeNull();
    for (const row of within(table).getAllByRole("row").slice(1)) {
      const cells = within(row).getAllByRole("cell");
      const sequence = Number(cells[0]?.querySelector("small.mono")?.textContent?.replace("#", ""));
      const source = demoEvents.items.find((event) => event.sequence === sequence)!;
      expect(cells[4]?.textContent).toBe(String(source.raw_score));
      expect(cells[5]?.textContent).toBe(typeof source.evidence.status === "string" ? source.evidence.status : "미제공");
    }
    expect(within(table).queryByText("SUSPICIOUS")).toBeNull();
  });

  it("keeps a raw-zero NORMAL event separate from the same player's SUSPICIOUS assessment", () => {
    const assessment = demoOverview.assessments.find((item) => item.session_id === "demo_esp_001" && item.player_id === "player_042")!;
    expect(assessment.status).toBe("SUSPICIOUS");
    withDemoEvent(4, { raw_score: 0, evidence: { synthetic: true, submodule: "module_integrity", status: "NORMAL" } }, () => {
      renderPage("events");
      const row = within(eventRow(4));
      expect(row.getByText("NORMAL")).toBeTruthy();
      expect(row.queryByText("SUSPICIOUS")).toBeNull();
      expect(row.getAllByRole("cell")[4]!.textContent).toBe("0");
      fireEvent.click(row.getByRole("button"));
      const drawer = screen.getByRole("dialog", { name: "이벤트 상세" });
      expect(within(drawer).getByRole("heading", { name: "현재 플레이어 판정" })).toBeTruthy();
      expect(within(drawer).getByText("SUSPICIOUS")).toBeTruthy();
    });
    withDemoEvent(4, { raw_score: 0, evidence: { synthetic: true, submodule: "module_integrity" } }, () => {
      renderPage("events");
      const row = within(eventRow(4));
      expect(row.getByText("미제공")).toBeTruthy();
      expect(row.queryByText("NORMAL")).toBeNull();
      expect(row.queryByText("SUSPICIOUS")).toBeNull();
    });
  });

  it("shows an explicit session elapsed time and observation clock without inventing legacy clocks", () => {
    withDemoEvent(4, { timestamp_ms: 84_000, time_basis: "session_relative", observed_at_utc: "2026-10-07T01:02:03Z", received_at_utc: "2026-10-07T01:02:04Z" }, () => {
      renderPage("events");
      const cell = within(eventRow(4)).getAllByRole("cell")[0]!;
      expect(cell.textContent).toContain("경과 01:24.000");
      expect(cell.textContent).toContain("관측 2026-10-07 10:02:03.000 KST");
      expect(cell.textContent).toContain("수신 2026-10-07 10:02:04.000 KST");
      expect(cell.querySelector("small.mono")?.textContent).toBe("#4");
    });
    withDemoEvent(4, { timestamp_ms: 84_000, time_basis: "unknown", observed_at_utc: undefined, received_at_utc: undefined }, () => {
      renderPage("events");
      const cell = within(eventRow(4)).getAllByRole("cell")[0]!;
      expect(cell.textContent).toContain("timestamp 84000 ms");
      expect(cell.textContent).toContain("관측 시각 미제공");
      expect(cell.textContent).not.toContain("경과 ");
      expect(cell.textContent).not.toContain("KST");
    });
  });

  it.each(["session_relative", "unknown"] as const)("reads bounded history-row times from canonical detail and preserves %s", async (basis) => {
    const { originals, history } = historyFixtures(9);
    for (const original of originals) {
      original.time_basis = basis;
      if (basis === "unknown") {
        original.observed_at_utc = null;
        // A conflicting raw declaration must not override Server unknown.
        original.evidence.time_basis = "unix_epoch_ms";
      }
    }
    const { fetchMock, restore } = mockLiveHistory(history, (id) => originals.find((event) => event.id === id));
    try {
      await connectLiveHistory();
      const row = await screen.findByRole("button", { name: "GodMode 사건 #1008 상세 보기" });
      await waitFor(() => {
        expect(row.textContent).toContain("수신 2026-10-07 19:12:46.123 KST");
        expect(row.textContent).toContain(basis === "session_relative" ? "경과 04:06.771" : "timestamp 246771 ms");
      });
      expect(document.querySelectorAll(".history-list li")).toHaveLength(8);
      const originalReads = fetchMock.mock.calls.map(([request]) => String(request)).filter((url) => url.includes("/api/dashboard/events/"));
      expect(originalReads).toHaveLength(8);
      expect(originalReads.some((url) => url.endsWith("history-original-0"))).toBe(false);
      if (basis === "session_relative") expect(row.textContent).toContain("관측 2026-10-07 19:12:44.957 KST");
      else {
        expect(row.textContent).toContain("관측 시각 미제공");
        expect(row.textContent).not.toContain("경과 ");
      }
      fireEvent.click(row);
      const drawer = screen.getByRole("dialog", { name: "이벤트 상세" });
      await waitFor(() => expect(within(drawer).getByText("수신 2026-10-07 19:12:46.123 KST")).toBeTruthy());
      expect(within(drawer).getAllByText(basis === "session_relative" ? "경과 04:06.771" : "timestamp 246771 ms").length).toBeGreaterThan(0);
      // Detail reads do not append history-only rows to the Event feed/counts.
      fireEvent.click(within(drawer).getByRole("button", { name: "상세 패널 닫기" }));
      visitPage("events");
      expect(screen.queryByText("History-only event 8")).toBeNull();
    } finally {
      restore();
    }
  });

  it("leaves mismatched history detail clocks unknown and allows a canonical retry", async () => {
    const { originals, history } = historyFixtures();
    let valid = false;
    const { restore } = mockLiveHistory(history, () => valid ? originals[0] : { ...originals[0], player_id: "wrong-player" });
    try {
      await connectLiveHistory();
      await waitFor(() => expect(screen.getByText("일부 이력의 원본 시간 정보를 불러오지 못했습니다.")).toBeTruthy());
      const row = screen.getByRole("button", { name: "GodMode 사건 #1000 상세 보기" });
      expect(row.textContent).toContain("timestamp 246771 ms");
      expect(row.textContent).toContain("관측 시각 미제공");
      expect(row.textContent).not.toContain("경과 ");
      expect(row.textContent).not.toContain("수신 ");
      valid = true;
      fireEvent.click(screen.getByRole("button", { name: "이력 시각 다시 확인" }));
      await waitFor(() => expect(row.textContent).toContain("관측 2026-10-07 19:12:44.957 KST"));
      expect(screen.queryByText("일부 이력의 원본 시간 정보를 불러오지 못했습니다.")).toBeNull();
    } finally {
      restore();
    }
  });

  it("aborts pending original-history reads when leaving the history tab and does not extend the feed", async () => {
    const { originals, history } = historyFixtures();
    let resolveDetail: ((event: DashboardEvent) => void) | undefined;
    const delayed = new Promise<DashboardEvent>((resolve) => { resolveDetail = resolve; });
    const { fetchMock, restore } = mockLiveHistory(history, () => delayed);
    try {
      await connectLiveHistory();
      await waitFor(() => expect(fetchMock.mock.calls.some(([request]) => String(request).includes("/api/dashboard/events/history-original-0"))).toBe(true));
      const pendingRead = fetchMock.mock.calls.find(([request]) => String(request).includes("/api/dashboard/events/history-original-0"))!;
      expect(pendingRead[1]?.signal?.aborted).toBe(false);
      expect(screen.getByRole("button", { name: "GodMode 사건 #1000 상세 보기" }).textContent).toContain("관측 시각 미제공");
      visitPage("events");
      expect(pendingRead[1]?.signal?.aborted).toBe(true);
      await act(async () => { resolveDetail?.(originals[0]!); await delayed; });
      expect(screen.queryByText("History-only event 0")).toBeNull();
      expect(screen.queryByText("관측 2026-10-07 19:12:44.957 KST")).toBeNull();
    } finally {
      restore();
    }
  });

  it("exposes explicit mixed Severity and falls back when a scoped dataset no longer supplies it", () => {
    const high = demoEvents.items.find((event) => event.sequence === 4)!;
    const critical = demoEvents.items.find((event) => event.sequence === 3)!;
    const savedHigh = high.evidence;
    const savedCritical = critical.evidence;
    try {
      high.evidence = { ...savedHigh, severity: "High" };
      critical.evidence = { ...savedCritical, severity: "Critical" };
      renderPage("events");
      const table = screen.getByRole("table", { name: "전체 이벤트" });
      expect(within(table).getByRole("columnheader", { name: "Severity" })).toBeTruthy();
      expect(within(eventRow(4)).getByText("High")).toBeTruthy();
      expect(within(eventRow(3)).getByText("Critical")).toBeTruthy();
      expect(eventRow(6).querySelector(".severity-badge")?.textContent).toBe("미제공");
      fireEvent.change(screen.getByLabelText("전체 이벤트 Severity"), { target: { value: "High" } });
      expect(within(table).getAllByRole("row").slice(1)).toHaveLength(1);
      fireEvent.change(screen.getByLabelText("전체 이벤트 정렬"), { target: { value: "severity:desc" } });
      showAdvancedFilters();
      fireEvent.change(screen.getByLabelText("모듈"), { target: { value: "esp" } });
      const scoped = screen.getByRole("table", { name: "전체 이벤트" });
      expect(within(scoped).queryByRole("columnheader", { name: "Severity" })).toBeNull();
      expect(screen.queryByLabelText("전체 이벤트 Severity")).toBeNull();
      expect((screen.getByLabelText("전체 이벤트 정렬") as HTMLSelectElement).value).toBe("sequence:desc");
      expect(eventRow(6)).toBeTruthy();
      expect(screen.queryByText("조건에 맞는 이벤트 없음")).toBeNull();
    } finally {
      cleanup();
      high.evidence = savedHigh;
      critical.evidence = savedCritical;
    }
  });

  it("shows verdict policy metrics instead of unprovided player score and confidence", () => {
    renderPage("players");
    fireEvent.click(screen.getByRole("button", { name: /player_042.*demo_esp_001/ }));
    const verdict = within(document.querySelector(".verdict-view") as HTMLElement);
    for (const label of ["독립 위험 근거", "활성 모듈", "중복 보정", "평가 상태"]) expect(verdict.getAllByText(label).length).toBeGreaterThan(0);
    for (const label of ["점수", "신뢰도", "최종 점수", "Score", "Confidence"]) expect(verdict.queryByText(label)).toBeNull();
    expect(document.querySelector(".verdict-metrics")?.textContent).not.toContain("미제공");
    expect(verdict.getByRole("region", { name: "플레이어 판정 근거" })).toBeTruthy();
  });

  it("opens the affected Launcher modules and routes their exact subject to player detail", () => {
    const status = demoOverview.launcher_statuses.find((item) => item.session_id === "demo_esp_001" && item.player_id === "player_042")!;
    const savedSource = status.source;
    const savedState = status.state;
    try {
      status.state = "degraded";
      status.source = { ...savedSource!, components: savedSource!.components.map((component) => {
        const raw = component.id === "esp" ? "FAILED" : component.id === "kernel_watcher" ? "WARN" : component.id === "hide_anywhere" ? "SKIPPED" : null;
        if (!raw) return component;
        const state = raw === "FAILED" ? "failed" as const : raw === "WARN" ? "degraded" as const : "stopped" as const;
        return { ...component, state, reported_status: state, required: raw !== "SKIPPED", details: { ...component.details, launcher_status: raw } };
      }) };
      render(<App />);
      fireEvent.click(screen.getByRole("button", { name: /Launcher .*문제 대상 확인/ }));
      expect(window.location.hash).toBe("#/system");
      expect(screen.getByRole("button", { name: "점검 대상만" }).getAttribute("aria-pressed")).toBe("true");
      const issues = screen.getByRole("region", { name: "Launcher 점검 대상" });
      const rows = within(issues).getAllByRole("row").slice(1);
      const affected = rows.filter((row) => row.textContent?.includes("demo_esp_001") && row.textContent?.includes("player_042"));
      expect(affected).toHaveLength(3);
      for (const raw of ["FAILED", "WARN", "SKIPPED"]) expect(affected.some((row) => row.textContent?.includes(raw))).toBe(true);
      expect(affected.every((row) => row.textContent?.includes(savedSource!.client_id))).toBe(true);
      fireEvent.click(within(issues).getAllByRole("button", { name: "demo_esp_001 player_042 상세 보기" })[0]!);
      expect(window.location.hash).toBe("#/players");
      expect((screen.getByLabelText("세션") as HTMLSelectElement).value).toBe("demo_esp_001");
      expect((screen.getByLabelText("플레이어") as HTMLSelectElement).value).toBe("player_042");
      expect(screen.getByRole("heading", { name: "player_042" })).toBeTruthy();
    } finally {
      cleanup();
      status.source = savedSource;
      status.state = savedState;
    }
  });

  it("clears earlier subject and query filters when opening detector observations from overview", () => {
    renderPage("events");
    showAdvancedFilters();
    fireEvent.change(screen.getByLabelText("세션"), { target: { value: "demo_noclip_001" } });
    fireEvent.change(screen.getByLabelText("플레이어"), { target: { value: "player_013" } });
    fireEvent.change(screen.getByLabelText("검색"), { target: { value: "Collision" } });
    fireEvent.change(screen.getByLabelText("판정"), { target: { value: "SUSPICIOUS" } });
    visitPage("overview");
    fireEvent.click(screen.getByRole("button", { name: "ESP 관측 1건 보기" }));
    expect(window.location.hash).toBe("#/events");
    expect((screen.getByLabelText("세션") as HTMLSelectElement).value).toBe("ALL");
    expect((screen.getByLabelText("플레이어") as HTMLSelectElement).value).toBe("ALL");
    expect((screen.getByLabelText("검색") as HTMLInputElement).value).toBe("");
    showAdvancedFilters();
    expect((screen.getByLabelText("모듈") as HTMLSelectElement).value).toBe("esp");
    expect((screen.getByLabelText("판정") as HTMLSelectElement).value).toBe("ALL");
    expect(eventRow(6)).toBeTruthy();
  });

  it("sorts the Event table and filters only explicitly supplied Severity", () => {
    renderPage("events");
    const sort = screen.getByLabelText("전체 이벤트 정렬");
    const sequences = () => within(screen.getByRole("table", { name: "전체 이벤트" })).getAllByRole("row").slice(1)
      .map((row) => Number(row.querySelector("td > small.mono")?.textContent?.replace("#", "")));
    expect(sequences()[0]).toBe(Math.max(...demoEvents.items.map((event) => event.sequence)));
    fireEvent.change(sort, { target: { value: "sequence:asc" } });
    expect(sequences()[0]).toBe(Math.min(...demoEvents.items.map((event) => event.sequence)));
    fireEvent.change(sort, { target: { value: "score:desc" } });
    const rawScores = [...document.querySelectorAll(".detection-table .raw-chip")].map((cell) => Number(cell.textContent));
    expect(rawScores[0]).toBe(Math.max(...demoEvents.items.map((event) => event.raw_score)));
    expect(rawScores).toEqual([...rawScores].sort((a, b) => b - a));
    expect(screen.queryByLabelText("전체 이벤트 Severity")).toBeNull();
    expect(sequences().length).toBeGreaterThan(0);
    expect(document.querySelector(".detection-table .severity-badge")).toBeNull();
  });

  it("connects Event investigation details to the authoritative player verdict without inventing score or confidence", () => {
    renderPage("events");
    fireEvent.click(screen.getByRole("button", { name: /player_042 .* 이벤트 #5 상세 보기/ }));
    const detail = screen.getByRole("dialog", { name: "이벤트 상세" });
    expect(within(detail).getByRole("heading", { name: "현재 플레이어 판정" })).toBeTruthy();
    expect(within(detail).getByText("SUSPICIOUS")).toBeTruthy();
    expect(within(detail).queryByText("미제공 / 미제공")).toBeNull();
    expect(within(detail).queryByText(/점수 \/ 신뢰도/)).toBeNull();
    expect(within(detail).getByText("CALIBRATED_ACTIVE_EVIDENCE, ASSESSMENT_INCOMPLETE")).toBeTruthy();
    fireEvent.click(within(detail).getByRole("button", { name: "플레이어 판정 확인" }));
    expect(window.location.hash).toBe("#/players");
    expect(screen.queryByRole("dialog", { name: "이벤트 상세" })).toBeNull();
    expect(screen.getByRole("heading", { name: "player_042" })).toBeTruthy();
    expect((screen.getByLabelText("세션") as HTMLSelectElement).value).toBe("demo_esp_001");
    expect((screen.getByLabelText("플레이어") as HTMLSelectElement).value).toBe("player_042");
  });

  it("renders an explicitly incomplete assessment as INCONCLUSIVE, not a normal result", () => {
    const assessment = demoOverview.assessments.find((item) => item.session_id === "demo_esp_001")!;
    const snapshot = demoSnapshots["demo_esp_001::player_042"]!;
    const savedAssessment = { ...assessment };
    const savedSnapshot = { ...snapshot };
    const incompleteVerdict = {
      ...assessment.final_verdict!,
      status: "INCONCLUSIVE" as const,
      assessment_complete: false,
      evidence_unit_count: 0,
      active_module_count: 0,
      active_modules: [],
      unresolved_modules: ["esp"],
      reason_codes: ["ASSESSMENT_INCOMPLETE"],
    };
    try {
      for (const value of [assessment, snapshot]) {
        Object.assign(value, {
          status: "INCONCLUSIVE",
          final_verdict: incompleteVerdict,
          reason_codes: incompleteVerdict.reason_codes,
        });
      }
      renderPage("players");
      fireEvent.click(screen.getByRole("button", { name: /player_042.*demo_esp_001/ }));
      const verdict = within(document.querySelector(".verdict-view") as HTMLElement);
      expect(verdict.getByText("INCONCLUSIVE")).toBeTruthy();
      expect(verdict.getByText("미완료")).toBeTruthy();
      expect(verdict.queryByText("점수")).toBeNull();
      expect(verdict.queryByText("신뢰도")).toBeNull();
      expect(verdict.getByText("독립 위험 근거")).toBeTruthy();
      expect(verdict.getByText("중복 보정")).toBeTruthy();
      expect(verdict.queryByText("NO_ACTIVE_EVIDENCE")).toBeNull();
      expect(verdict.queryByText("SUSPICIOUS")).toBeNull();
    } finally {
      cleanup();
      Object.assign(assessment, savedAssessment);
      Object.assign(snapshot, savedSnapshot);
    }
  });

  it("moves from a protection module to its filtered Event page", () => {
    renderPage("modules");
    fireEvent.click(screen.getByRole("button", { name: "ESP 이벤트 보기" }));
    expect(screen.getByRole("link", { name: "이벤트" }).getAttribute("aria-current")).toBe("page");
    expect(window.location.hash).toBe("#/events");
    showAdvancedFilters();
    expect((screen.getByLabelText("모듈") as HTMLSelectElement).value).toBe("esp");
    expect(document.querySelectorAll(".module-entry")).toHaveLength(0);
  });

  it("moves from a session to its players without displaying the other sessions", () => {
    renderPage("sessions");
    fireEvent.click(screen.getByRole("button", { name: "demo_esp_001" }));
    expect(window.location.hash).toBe("#/players");
    expect(screen.getByRole("link", { name: "플레이어" }).getAttribute("aria-current")).toBe("page");
    expect((screen.getByLabelText("세션") as HTMLSelectElement).value).toBe("demo_esp_001");
    expect(screen.getByRole("button", { name: /player_042.*demo_esp_001/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /player_013.*demo_noclip_001/ })).toBeNull();
  });

  it("splits player detail content into accessible tabs without changing the selected player", () => {
    renderPage("players");
    fireEvent.click(screen.getByRole("button", { name: /player_042.*demo_esp_001/ }));
    expect(screen.getByRole("tab", { name: "판정" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByText("신뢰도")).toBeNull();
    expect(document.querySelectorAll(".module-signals-table tbody tr")).toHaveLength(0);
    fireEvent.click(screen.getByRole("tab", { name: "모듈 신호" }));
    expect(screen.getByRole("tab", { name: "모듈 신호" }).getAttribute("aria-selected")).toBe("true");
    expect(document.querySelectorAll(".module-signals-table tbody tr").length).toBeGreaterThan(0);
    expect(screen.queryByText("근거 단위")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "GodMode 이력" }));
    expect(screen.getByRole("heading", { name: "GodMode 사건 이력" })).toBeTruthy();
    expect(document.querySelectorAll(".module-signals-table tbody tr")).toHaveLength(0);
    fireEvent.click(screen.getByRole("tab", { name: "판정" }));
    expect(screen.getByRole("heading", { name: "player_042" })).toBeTruthy();
  });

  it("sorts module signals by latest or loaded-history highest raw without replacing the current signal", () => {
    const template = demoEvents.items.find((event) => event.sequence === 6)!;
    const oldHigh: DashboardEvent = {
      ...template, id: "00000000-0000-4000-8000-000000009990", sequence: 0,
      raw_score: 20, timestamp_ms: 50_000, evidence: { synthetic: true }, reasons: ["OLDER HIGH RAW OBSERVATION"],
    };
    demoEvents.items.push(oldHigh);
    try {
      renderPage("players");
      fireEvent.click(screen.getByRole("button", { name: /player_042.*demo_esp_001/ }));
      fireEvent.click(screen.getByRole("tab", { name: "모듈 신호" }));
      const rows = () => [...document.querySelectorAll<HTMLTableRowElement>(".module-signals-table tbody tr")];
      expect(rows()[0]!.textContent).toContain("외부 프로세스");
      expect(within(rows()[0]!).getAllByRole("cell")[2]!.textContent).toBe("7");
      fireEvent.change(screen.getByLabelText("모듈 신호 정렬"), { target: { value: "highest" } });
      expect(rows()[0]!.textContent).toContain("ESP 접근 감시");
      expect(within(rows()[0]!).getAllByRole("cell")[2]!.textContent).toBe("1");
      expect(rows()[0]!.textContent).toContain("raw 20");
      expect(rows()[0]!.textContent).not.toContain("OLDER HIGH RAW OBSERVATION");
      fireEvent.click(within(rows()[0]!).getByRole("button", { name: "ESP 접근 감시 이력 최고 관측 상세" }));
      const detail = screen.getByRole("dialog", { name: "이벤트 상세" });
      expect(within(detail).getByText("OLDER HIGH RAW OBSERVATION")).toBeTruthy();
    } finally {
      cleanup();
      demoEvents.items.splice(demoEvents.items.indexOf(oldHigh), 1);
    }
  });

  it("moves selection and keyboard focus together through the player detail tabs", () => {
    renderPage("players");
    const assertSelected = (name: string) => {
      const tab = screen.getByRole("tab", { name });
      expect(tab.getAttribute("aria-selected")).toBe("true");
      expect(tab.getAttribute("tabindex")).toBe("0");
      expect(document.activeElement).toBe(tab);
      expect(screen.getAllByRole("tab").filter((item) => item.getAttribute("aria-selected") === "true")).toHaveLength(1);
      expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe(tab.id);
      return tab;
    };
    const verdict = screen.getByRole("tab", { name: "판정" });
    verdict.focus();
    fireEvent.keyDown(verdict, { key: "ArrowRight" });
    fireEvent.keyDown(assertSelected("타임라인"), { key: "ArrowLeft" });
    fireEvent.keyDown(assertSelected("판정"), { key: "ArrowLeft" });
    fireEvent.keyDown(assertSelected("GodMode 이력"), { key: "ArrowRight" });
    fireEvent.keyDown(assertSelected("판정"), { key: "End" });
    fireEvent.keyDown(assertSelected("GodMode 이력"), { key: "Home" });
    assertSelected("판정");
  });

  it("opens an unfiltered Event list from overview even when the previous page had active filters", () => {
    renderPage("events");
    showAdvancedFilters();
    fireEvent.change(screen.getByLabelText("모듈"), { target: { value: "esp" } });
    fireEvent.change(screen.getByLabelText("세션"), { target: { value: "demo_esp_001" } });
    fireEvent.change(screen.getByLabelText("플레이어"), { target: { value: "player_042" } });
    fireEvent.change(screen.getByLabelText("검색"), { target: { value: "player_042" } });
    expect(screen.queryByText("Collision Disabled Too Long")).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "종합 현황" }));
    const allEvents = screen.getByRole("button", { name: /^탐지 기록/ });
    expect(allEvents.querySelector("strong")?.textContent).toBe(String(demoEvents.items.filter((event) => event.event_kind === "detection").length));
    fireEvent.click(allEvents);
    expect(window.location.hash).toBe("#/events");
    expect((screen.getByLabelText("검색") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("세션") as HTMLSelectElement).value).toBe("ALL");
    expect((screen.getByLabelText("플레이어") as HTMLSelectElement).value).toBe("ALL");
    showAdvancedFilters();
    expect((screen.getByLabelText("모듈") as HTMLSelectElement).value).toBe("ALL");
    expect(screen.getByText("Collision Disabled Too Long")).toBeTruthy();
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
      renderPage("players");
      fireEvent.click(screen.getByRole("button", { name: "연결" }));
      const dialog = screen.getByRole("dialog");
      fireEvent.change(within(dialog).getByLabelText("Dashboard 토큰"), { target: { value: "test-token" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "연결" }));

      await waitFor(() => expect(screen.getByText("LIVE")).toBeTruthy());
      fireEvent.click(screen.getByRole("button", { name: /player_042.*demo_esp_001/ }));
      await waitFor(() => expect(screen.getByRole("heading", { name: "player_042" })).toBeTruthy());
      fireEvent.click(screen.getByRole("tab", { name: "모듈 신호" }));
      await waitFor(() => {
        const moduleCards = [...document.querySelectorAll(".module-signals-table tbody tr")];
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

      const moduleCards = [...document.querySelectorAll(".module-signals-table tbody tr")];
      expect(moduleCards.some((card) => card.textContent?.includes("입력 행동"))).toBe(false);
      fireEvent.click(screen.getByRole("tab", { name: "GodMode 이력" }));
      expect(screen.queryByText("OLD SUBJECT HISTORY")).toBeNull();
      fireEvent.click(screen.getByRole("tab", { name: "판정" }));
      expect(screen.getByRole("heading", { name: "player_042" })).toBeTruthy();
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
      renderPage("players");
      fireEvent.click(screen.getByRole("button", { name: "연결" }));
      const dialog = screen.getByRole("dialog");
      fireEvent.change(within(dialog).getByLabelText("Dashboard 토큰"), { target: { value: "test-token" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "연결" }));

      await waitFor(() => expect(screen.getByText("LIVE")).toBeTruthy());
      fireEvent.click(screen.getByRole("tab", { name: "모듈 신호" }));
      await waitFor(() => expect(document.querySelectorAll(".module-signals-table tbody tr").length).toBeGreaterThan(0));
      fireEvent.click(screen.getByRole("tab", { name: "GodMode 이력" }));
      await waitFor(() => expect(screen.getByText("Dashboard 데이터 저장소를 현재 사용할 수 없습니다.")).toBeTruthy());
      fireEvent.click(screen.getByRole("tab", { name: "모듈 신호" }));
      const detailBeforeFailure = [...document.querySelectorAll(".module-signals-table tbody tr")].map((card) => card.textContent).join(" ");

      failRefresh = true;
      fireEvent.click(screen.getByRole("button", { name: "새로고침" }));
      await waitFor(() => expect(screen.getByText("LIVE · 지연")).toBeTruthy());
      const detailAfterFailure = [...document.querySelectorAll(".module-signals-table tbody tr")].map((card) => card.textContent).join(" ");
      expect(detailAfterFailure).toBe(detailBeforeFailure);
    } finally {
      vi.unstubAllGlobals();
      HTMLDialogElement.prototype.showModal = originalShowModal;
      HTMLDialogElement.prototype.close = originalClose;
    }
  });
});
