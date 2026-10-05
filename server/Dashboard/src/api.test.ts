import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DashboardApiError,
  fetchEventDetail,
  fetchEvents,
  fetchGodModeHistory,
  fetchOverview,
  fetchSnapshot,
  fetchSubjectStatus,
  loadSubjectDetail,
  loadSubjectDetailParts,
} from "./api";
import {
  demoEventItems,
  demoEvents,
  demoOverview,
  demoSnapshots,
  demoStatuses,
} from "./mockData";
import type { GodModeHistoryResponse, LiveConnectionInput, SnapshotResponse, SubjectStatusResponse } from "./types";

const connection: LiveConnectionInput = {
  baseUrl: "http://dashboard.test",
  token: "test-token",
};

const snapshot: SnapshotResponse = {
  session_id: "session_001",
  player_id: "player_001",
  status: "UNKNOWN",
  score: null,
  confidence: null,
  assessment_available: false,
  final_verdict: null,
  reason_codes: [],
  data_state: "missing",
  modules: [],
  policy: {},
};

const status: SubjectStatusResponse = {
  session_id: "session_001",
  player_id: "player_001",
  state: "healthy",
  sources: [],
  launcher: {
    state: "healthy",
    connected: true,
    source: null,
    reason: "",
  },
  has_more_sources: false,
  reason: "",
};

const history: GodModeHistoryResponse = {
  items: [{
    event_id: "00000000-0000-4000-8000-000000000099",
    sequence: 99,
    session_id: "session_001",
    player_id: "player_001",
    module: "godmode",
    timestamp_ms: 12_000,
    raw_score: 3,
    evidence: { health_delta: 100 },
    reasons: ["Health changed without a valid game event"],
  }],
  has_more: false,
  next_after_sequence: null,
  final_assessment: false,
};

function jsonResponse(body: unknown, responseStatus = 200): Response {
  return {
    ok: responseStatus >= 200 && responseStatus < 300,
    status: responseStatus,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
  } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("subject detail API", () => {
  it("lets runtime status resolve even when the scoring snapshot fails", async () => {
    const fetchMock = vi.fn((request: RequestInfo | URL) => {
      const url = String(request);
      if (url.endsWith("/snapshot")) {
        return Promise.resolve(jsonResponse({ detail: "missing" }, 404));
      }
      if (url.endsWith("/status")) {
        return Promise.resolve(jsonResponse(status));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    const requests = loadSubjectDetailParts(
      connection,
      "session_001",
      "player_001",
    );

    await expect(requests.status).resolves.toEqual(status);
    await expect(requests.snapshot).rejects.toMatchObject({
      name: "DashboardApiError",
      status: 404,
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("preserves the legacy combined detail result when both reads succeed", async () => {
    vi.stubGlobal("fetch", vi.fn((request: RequestInfo | URL) => {
      const url = String(request);
      return Promise.resolve(jsonResponse(url.endsWith("/snapshot") ? snapshot : status));
    }));

    await expect(loadSubjectDetail(
      connection,
      "session_001",
      "player_001",
    )).resolves.toEqual({ snapshot, status });
  });
});

describe("Dashboard response contracts", () => {
  it("accepts the current backend-v2 shaped demo responses", async () => {
    const demoSnapshot = Object.values(demoSnapshots)[0]!;
    const demoStatus = Object.values(demoStatuses)[0]!;
    const demoEvent = demoEventItems[0]!;
    const fetchMock = vi.fn((request: RequestInfo | URL) => {
      const url = new URL(String(request));
      if (url.pathname === "/api/dashboard/overview") return Promise.resolve(jsonResponse(demoOverview));
      if (url.pathname === "/api/dashboard/events") return Promise.resolve(jsonResponse(demoEvents));
      if (url.pathname.endsWith("/snapshot")) return Promise.resolve(jsonResponse(demoSnapshot));
      if (url.pathname.endsWith("/status")) return Promise.resolve(jsonResponse(demoStatus));
      if (url.pathname.endsWith("/history")) return Promise.resolve(jsonResponse(history));
      if (url.pathname.startsWith("/api/dashboard/events/")) return Promise.resolve(jsonResponse(demoEvent));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchOverview(connection)).resolves.toEqual(demoOverview);
    await expect(fetchEvents(connection)).resolves.toEqual(demoEvents);
    await expect(fetchEventDetail(connection, demoEvent.id)).resolves.toEqual(demoEvent);
    await expect(fetchSnapshot(connection, demoSnapshot.session_id, demoSnapshot.player_id)).resolves.toEqual(demoSnapshot);
    await expect(fetchSubjectStatus(connection, demoStatus.session_id, demoStatus.player_id)).resolves.toEqual(demoStatus);
    await expect(fetchGodModeHistory(connection, "session_001", "player_001")).resolves.toEqual(history);
  });

  it("normalizes the backend status response used when heartbeat storage is unavailable", async () => {
    const demoStatus = Object.values(demoStatuses)[0]!;
    const { has_more_sources: _omitted, ...withoutPaginationFlag } = demoStatus;
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(withoutPaginationFlag))));

    await expect(fetchSubjectStatus(connection, demoStatus.session_id, demoStatus.player_id)).resolves.toEqual({
      ...withoutPaginationFlag,
      has_more_sources: false,
    });
  });

  it.each([
    {
      name: "overview nested counts",
      body: { ...demoOverview, counts: { ...demoOverview.counts, events: "4" } },
      path: "counts.events",
      request: () => fetchOverview(connection),
    },
    {
      name: "events nested Event",
      body: {
        ...demoEvents,
        items: [{ ...demoEventItems[0]!, raw_score: "3" }],
      },
      path: "items[0].raw_score",
      request: () => fetchEvents(connection),
    },
    {
      name: "event detail",
      body: { ...demoEventItems[0]!, sequence: -1 },
      path: "$.sequence",
      request: () => fetchEventDetail(connection, demoEventItems[0]!.id),
    },
    {
      name: "snapshot modules",
      body: { ...Object.values(demoSnapshots)[0]!, modules: {} },
      path: "modules",
      request: () => {
        const value = Object.values(demoSnapshots)[0]!;
        return fetchSnapshot(connection, value.session_id, value.player_id);
      },
    },
    {
      name: "status launcher",
      body: {
        ...Object.values(demoStatuses)[0]!,
        launcher: { ...Object.values(demoStatuses)[0]!.launcher, connected: "yes" },
      },
      path: "launcher.connected",
      request: () => {
        const value = Object.values(demoStatuses)[0]!;
        return fetchSubjectStatus(connection, value.session_id, value.player_id);
      },
    },
    {
      name: "GodMode history item",
      body: { ...history, items: [{ ...history.items[0]!, module: "noclip" }] },
      path: "items[0].module",
      request: () => fetchGodModeHistory(connection, "session_001", "player_001"),
    },
  ])("rejects a malformed $name response without returning partial data", async ({ body, path, request }) => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(body))));

    await expect(request()).rejects.toEqual(expect.objectContaining({
      name: "DashboardApiError",
      status: 200,
      message: expect.stringContaining(path),
    }));
    await expect(request()).rejects.toBeInstanceOf(DashboardApiError);
  });
});
