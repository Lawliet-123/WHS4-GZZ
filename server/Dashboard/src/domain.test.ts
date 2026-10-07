import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DashboardApiError,
  loadDashboardBundle,
  loadEventDetail,
  loadSubjectDetail,
} from "./api";
import {
  buildTimelineBuckets,
  defaultFilters,
  filterAssessments,
  filterEvents,
  formatElapsed,
  shortenId,
  verdictMeta,
} from "./domain";
import { demoEvents, demoOverview, demoSnapshots, demoStatuses } from "./mockData";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Dashboard backend-v2 domain", () => {
  it("keeps the four projection statuses distinct, including UNKNOWN", () => {
    expect(Object.keys(verdictMeta)).toEqual([
      "SUSPICIOUS",
      "INCONCLUSIVE",
      "NO_ACTIVE_EVIDENCE",
      "UNKNOWN",
    ]);
    expect(verdictMeta.UNKNOWN.tone).toBe("neutral");
    expect(verdictMeta.UNKNOWN.label).not.toBe(verdictMeta.NO_ACTIVE_EVIDENCE.label);
  });

  it("preserves backend-owned nullable score and confidence values", () => {
    for (const assessment of demoOverview.assessments) {
      expect(assessment.score).toBeNull();
      expect(assessment.confidence).toBeNull();
    }
    for (const snapshot of Object.values(demoSnapshots)) {
      expect(snapshot.score).toBeNull();
      expect(snapshot.confidence).toBeNull();
    }
    expect(demoOverview.sessions.every((session) => session.status === "UNKNOWN")).toBe(true);
  });

  it("filters nested evidence, module and server verdict without calculating a verdict", () => {
    const filters = {
      ...defaultFilters,
      module: "esp",
      verdict: "SUSPICIOUS" as const,
      query: "window_overlap",
    };
    const result = filterEvents(demoEvents.items, demoOverview, filters);
    expect(result).toHaveLength(1);
    expect(result[0]?.module).toBe("esp");
    expect(result[0]?.raw_score).toBe(1);
    expect(demoOverview.assessments.find((item) => item.session_id === "demo_esp_001")?.score).toBeNull();
  });

  it("keeps calibrated external access evidence active while ESP remains unresolved", () => {
    const assessment = demoOverview.assessments.find((item) => item.session_id === "demo_esp_001")!;
    const snapshot = demoSnapshots["demo_esp_001::player_042"]!;
    expect(assessment.final_verdict).toMatchObject({
      status: "SUSPICIOUS",
      assessment_complete: false,
      evidence_unit_count: 1,
      active_module_count: 1,
      active_modules: ["external_access"],
      unresolved_modules: ["esp"],
      reason_codes: ["CALIBRATED_ACTIVE_EVIDENCE", "ASSESSMENT_INCOMPLETE"],
    });
    expect(snapshot.final_verdict).toEqual(assessment.final_verdict);
    expect(snapshot.modules.find((item) => item.module === "external_access")).toMatchObject({
      raw_score: 7,
      evidence: {
        submodule: "aggregate",
        scoped_submodules: {
          external_process: { raw_score: 7 },
          module_integrity: { raw_score: 0, status: "NORMAL" },
        },
      },
    });
    const policyModules = snapshot.policy.modules as Array<{
      state: { module: string };
      evaluation: { annotations: { notes: string[] } };
    }>;
    const accessNotes = policyModules.find((item) => item.state.module === "external_access")!.evaluation.annotations.notes.join(" ");
    expect(accessNotes).toContain("external_process는 scoped threshold 2");
    expect(accessNotes).toContain("module_integrity는 event_threshold 2");
    expect(accessNotes).toContain("과거 기준 충족 사건을 지우지 않는다");
    expect(accessNotes).not.toContain("pending");
    expect(policyModules.find((item) => item.state.module === "esp")!.evaluation.annotations.notes.join(" "))
      .toContain("pending");
  });

  it("uses stable event IDs when elapsed timestamps are duplicated", () => {
    const repeated = demoEvents.items.filter((item) => item.timestamp_ms === 84_438);
    expect(repeated).toHaveLength(2);
    expect(new Set(repeated.map((item) => item.id)).size).toBe(2);
    expect(new Set(repeated.map((item) => item.sequence)).size).toBe(2);
  });

  it("filters assessments by module presence and exact final status", () => {
    const result = filterAssessments(demoOverview.assessments, demoEvents.items, {
      ...defaultFilters,
      module: "noclip",
      verdict: "SUSPICIOUS",
    });
    expect(result.map((item) => item.id)).toEqual(["demo_noclip_001:player_013"]);
  });

  it("filters logical services through their real Event aliases and submodules", () => {
    const moduleIntegrity = filterEvents(demoEvents.items, demoOverview, {
      ...defaultFilters,
      module: "module_integrity",
    });
    expect(moduleIntegrity).toHaveLength(1);
    expect(moduleIntegrity[0]).toMatchObject({
      module: "external_access",
      evidence: { submodule: "module_integrity" },
    });

    const externalAccess = filterEvents(demoEvents.items, demoOverview, {
      ...defaultFilters,
      module: "external_access",
    });
    expect(externalAccess).toHaveLength(1);
    expect(externalAccess[0]).toMatchObject({
      module: "external_access",
      evidence: { submodule: "external_process" },
    });
  });

  it("separates detection and operational Events without inventing a verdict", () => {
    const operational = filterEvents(demoEvents.items, demoOverview, {
      ...defaultFilters,
      eventKind: "operational",
    });
    expect(operational.length).toBeGreaterThan(0);
    expect(operational.every((item) => item.event_kind === "operational")).toBe(true);

    const subjects = filterAssessments(demoOverview.assessments, demoEvents.items, {
      ...defaultFilters,
      eventKind: "operational",
    });
    expect(subjects.map((item) => item.id)).toEqual(["demo_esp_001:player_042"]);
  });

  it("keeps only subjects whose assessment or event actually matches the search query", () => {
    const result = filterAssessments(demoOverview.assessments, demoEvents.items, {
      ...defaultFilters,
      query: "Collision Disabled Too Long",
    });
    expect(result.map((item) => item.id)).toEqual(["demo_noclip_001:player_013"]);

    const noMatch = filterAssessments(demoOverview.assessments, demoEvents.items, {
      ...defaultFilters,
      query: "not-present-in-any-assessment-or-event",
    });
    expect(noMatch).toEqual([]);
  });

  it("builds fixed timeline buckets and counts operational events separately", () => {
    const buckets = buildTimelineBuckets(demoEvents.items, 8);
    expect(buckets).toHaveLength(8);
    expect(buckets.reduce((sum, item) => sum + item.total, 0)).toBe(demoEvents.items.length);
    expect(buckets.reduce((sum, item) => sum + item.operational, 0)).toBe(demoEvents.items.filter((item) => item.event_kind === "operational").length);
  });

  it("formats elapsed time and safely shortens long client identifiers", () => {
    expect(formatElapsed(65_000)).toBe("01:05");
    expect(formatElapsed(3_665_000)).toBe("01:01:05");
    expect(formatElapsed(Number.NaN)).toBe("--:--");
    const value = "BP_FirstPersonCharacter_Hunter_Default_C_2147479755";
    const shortened = shortenId(value, 26);
    expect(shortened.length).toBe(26);
    expect(shortened).toContain("…");
  });
});

describe("Dashboard backend-v2 API", () => {
  const connection = { baseUrl: "http://127.0.0.1:8002/", token: "dashboard-test" };

  function json(value: unknown, status = 200): Response {
    return new Response(JSON.stringify(value), {
      status,
      headers: { "content-type": "application/json; charset=utf-8" },
    });
  }

  it("loads overview and events with a Bearer token and backend query names", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const headers = init?.headers as Record<string, string>;
      expect(headers.Authorization).toBe("Bearer dashboard-test");
      if (url.includes("/overview")) return json(demoOverview);
      if (url.includes("/events")) return json(demoEvents);
      throw new Error(`unexpected URL ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const bundle = await loadDashboardBundle(connection, {
      ...defaultFilters,
      sessionId: "demo_esp_001",
      playerId: "player_042",
      module: "esp",
      query: "overlay",
    });

    expect(bundle.overview).toStrictEqual(demoOverview);
    expect(bundle.events).toStrictEqual(demoEvents);
    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls.some((url) => url.includes("session_id=demo_esp_001") && url.includes("player_id=player_042"))).toBe(true);
    expect(urls.some((url) => url.includes("module=esp") && url.includes("q=overlay"))).toBe(true);
    expect(bundle.load_state.overview).toMatchObject({ complete: true, truncated: false, pages_loaded: 1 });
    expect(bundle.load_state.events).toMatchObject({ complete: true, truncated: false, pages_loaded: 1 });
  });

  it("follows overview session cursors and merges page collections by stable IDs", async () => {
    const firstSession = demoOverview.sessions[0]!;
    const secondSession = demoOverview.sessions[1]!;
    const sharedPlayer = {
      ...demoOverview.players[0]!,
      session_ids: [firstSession.id],
    };
    const firstOverview = {
      ...demoOverview,
      sessions: [firstSession],
      players: [sharedPlayer],
      assessments: [demoOverview.assessments[0]!],
      module_statuses: [demoOverview.module_statuses[0]!],
      launcher_statuses: [demoOverview.launcher_statuses[1]!],
      session_page: { next_after_session: firstSession.id, has_more: true },
    };
    const secondOverview = {
      ...demoOverview,
      sessions: [secondSession],
      players: [
        { ...sharedPlayer, session_ids: [secondSession.id] },
        demoOverview.players[1]!,
      ],
      assessments: [demoOverview.assessments[1]!],
      module_statuses: [demoOverview.module_statuses[0]!],
      launcher_statuses: [demoOverview.launcher_statuses[0]!],
      session_page: { next_after_session: null, has_more: false },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/overview")) {
        return json(url.includes("after_session=") ? secondOverview : firstOverview);
      }
      if (url.includes("/events")) return json(demoEvents);
      throw new Error(`unexpected URL ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const bundle = await loadDashboardBundle(connection);

    expect(bundle.overview.counts).toStrictEqual(firstOverview.counts);
    expect(bundle.overview.connection.Receiver).toStrictEqual(firstOverview.connection.Receiver);
    expect(bundle.overview.connection.Scoring).toStrictEqual(firstOverview.connection.Scoring);
    expect(bundle.overview.connection.Launcher).toMatchObject({
      state: "degraded",
      scope: "all_returned_sessions",
      observed_pairs: 2,
    });
    expect(bundle.overview.sessions.map((item) => item.id)).toEqual([firstSession.id, secondSession.id]);
    expect(bundle.overview.players[0]?.session_ids).toEqual([firstSession.id, secondSession.id]);
    expect(bundle.overview.assessments).toHaveLength(2);
    expect(bundle.overview.module_statuses).toHaveLength(1);
    expect(bundle.overview.launcher_statuses).toHaveLength(2);
    expect(bundle.overview.session_page).toEqual({ next_after_session: null, has_more: false });
    expect(bundle.load_state.overview).toEqual({
      pages_loaded: 2,
      items_loaded: 2,
      complete: true,
      truncated: false,
      truncation_reason: null,
    });
  });

  it("reads every event cursor page and deduplicates stable event IDs", async () => {
    const firstEvent = demoEvents.items[0]!;
    const secondEvent = demoEvents.items[1]!;
    const firstEvents = {
      ...demoEvents,
      items: [firstEvent],
      next_cursor: "event-page-2",
      has_more: true,
    };
    const secondEvents = {
      ...demoEvents,
      items: [firstEvent, secondEvent],
      next_cursor: null,
      has_more: false,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/overview")) return json(demoOverview);
      if (url.includes("cursor=event-page-2")) return json(secondEvents);
      if (url.includes("/events")) return json(firstEvents);
      throw new Error(`unexpected URL ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const bundle = await loadDashboardBundle(connection);

    expect(bundle.events.items.map((item) => item.id)).toEqual([firstEvent.id, secondEvent.id]);
    expect(bundle.events.has_more).toBe(false);
    expect(bundle.load_state.events).toEqual({
      pages_loaded: 2,
      items_loaded: 2,
      complete: true,
      truncated: false,
      truncation_reason: null,
    });
  });

  it("reports an explicit truncation state at the event safety limit", async () => {
    let eventPage = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/overview")) return json(demoOverview);
      if (url.includes("/events")) {
        eventPage += 1;
        return json({
          ...demoEvents,
          items: [{ ...demoEvents.items[0]!, id: `event-${eventPage}`, sequence: eventPage }],
          next_cursor: `cursor-${eventPage}`,
          has_more: true,
          through_sequence: 100_000,
        });
      }
      throw new Error(`unexpected URL ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const bundle = await loadDashboardBundle(connection);

    expect(eventPage).toBe(250);
    expect(bundle.events.has_more).toBe(true);
    expect(bundle.load_state.events).toEqual({
      pages_loaded: 250,
      items_loaded: 250,
      complete: false,
      truncated: true,
      truncation_reason: "page_limit",
    });
  });

  it("marks malformed pagination as truncated instead of silently claiming completion", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/overview")) return json(demoOverview);
      if (url.includes("/events")) return json({ ...demoEvents, has_more: true, next_cursor: null });
      throw new Error(`unexpected URL ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const bundle = await loadDashboardBundle(connection);
    expect(bundle.load_state.events).toMatchObject({
      complete: false,
      truncated: true,
      truncation_reason: "missing_cursor",
    });
  });

  it("loads snapshot, component status and an event detail from their v2 routes", async () => {
    const snapshot = demoSnapshots["demo_esp_001::player_042"]!;
    const status = demoStatuses["demo_esp_001::player_042"]!;
    const detail = demoEvents.items[0]!;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/snapshot")) return json(snapshot);
      if (url.endsWith("/status")) return json(status);
      if (url.includes("/events/")) return json(detail);
      throw new Error(`unexpected URL ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(loadSubjectDetail(connection, "demo_esp_001", "player_042")).resolves.toEqual({ snapshot, status });
    await expect(loadEventDetail(connection, detail.id)).resolves.toEqual(detail);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("surfaces live failures and never switches to demo data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(loadDashboardBundle(connection)).rejects.toBeInstanceOf(DashboardApiError);
  });

  it("maps backend authentication failures without exposing response bodies", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: "private server text" }, 401)));
    await expect(loadEventDetail(connection, "event-1")).rejects.toMatchObject({
      status: 401,
      message: "Dashboard 인증 정보가 올바르지 않습니다.",
    });
  });

  it("propagates caller cancellation through bundle and detail loaders", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    controller.abort();

    await expect(loadDashboardBundle(connection, undefined, 0, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    await expect(loadSubjectDetail(connection, "demo_esp_001", "player_042", controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    await expect(loadEventDetail(connection, "event-1", controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
