import { afterEach, describe, expect, it, vi } from "vitest";
import { loadSubjectDetail, loadSubjectDetailParts } from "./api";
import type { LiveConnectionInput, SnapshotResponse, SubjectStatusResponse } from "./types";

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
