import { describe, expect, it } from "vitest";
import { detectorObservationCounts, sessionObservationFlow, verdictDistribution } from "./chartData";
import type { Assessment, DashboardEvent, VerdictStatus } from "./types";

function assessment(sessionId: string, playerId: string, status: VerdictStatus): Assessment {
  return { id: `${sessionId}:${playerId}`, session_id: sessionId, player_id: playerId, status, assessment_available: status !== "UNKNOWN", score: null, confidence: null, final_verdict: null, reason_codes: [], data_state: status === "UNKNOWN" ? "missing" : "available", module_scores: [], reasons: [] };
}

function event(sequence: number, changes: Partial<DashboardEvent> = {}): DashboardEvent {
  return { id: `event-${sequence}`, sequence, session_id: "session-A", player_id: "player-A", module: "esp", timestamp_ms: sequence * 1_000, evidence: {}, reasons: [], raw_score: 0, event_kind: "detection", time_basis: "unknown", evidence_image: null, log_excerpt: null, ...changes };
}

describe("server verdict distribution", () => {
  it("returns all four statuses in a fixed order even when empty", () => {
    expect(verdictDistribution([])).toEqual([
      { status: "SUSPICIOUS", count: 0 },
      { status: "INCONCLUSIVE", count: 0 },
      { status: "NO_ACTIVE_EVIDENCE", count: 0 },
      { status: "UNKNOWN", count: 0 },
    ]);
  });

  it("counts the last current verdict for each session/player pair without mutating input", () => {
    const values = [
      assessment("session-A", "player-A", "SUSPICIOUS"),
      assessment("session-B", "player-A", "UNKNOWN"),
      assessment("session-A", "player-B", "NO_ACTIVE_EVIDENCE"),
      assessment("session-A", "player-A", "INCONCLUSIVE"),
    ];
    const before = structuredClone(values);
    expect(verdictDistribution(values)).toEqual([
      { status: "SUSPICIOUS", count: 0 },
      { status: "INCONCLUSIVE", count: 1 },
      { status: "NO_ACTIVE_EVIDENCE", count: 1 },
      { status: "UNKNOWN", count: 1 },
    ]);
    expect(values).toEqual(before);
  });

  it("does not collapse distinct pairs containing delimiters or use score to produce a verdict", () => {
    const values = [
      { ...assessment("session:part", "player", "UNKNOWN"), score: 999 },
      assessment("session", "part:player", "SUSPICIOUS"),
    ];
    const result = verdictDistribution(values);
    expect(result.find((item) => item.status === "UNKNOWN")?.count).toBe(1);
    expect(result.find((item) => item.status === "SUSPICIOUS")?.count).toBe(1);
  });
});

describe("logical detector observation counts", () => {
  it("returns no made-up detector rows when there are no detections", () => {
    expect(detectorObservationCounts([])).toEqual([]);
    expect(detectorObservationCounts([event(1, { event_kind: "operational", raw_score: 100 })])).toEqual([]);
  });

  it("preserves DLL submodule ownership, aliases and unmapped detectors without summing raw scores", () => {
    const values = [
      event(1, { module: "external_access", evidence: { submodule: "module_integrity" }, raw_score: 2 }),
      event(2, { module: "module_integrity", raw_score: 100 }),
      event(3, { module: "external_access", evidence: { submodule: "module_integrity" } }),
      event(4, { module: "external_access", evidence: { submodule: "external_process" }, raw_score: 7 }),
      event(5, { module: "overlay_hook", raw_score: -1 }),
      event(6, { module: "memory_integrity", raw_score: 3 }),
      event(7, { module: "future_detector", raw_score: 1 }),
      event(8, { module: "future_detector", event_kind: "operational", raw_score: 9 }),
    ];
    const result = detectorObservationCounts(values);
    expect(result[0]).toEqual({ id: "module_integrity", label: "게임 모듈 무결성", count: 3, positiveCount: 2 });
    expect(result.find((item) => item.id === "external_access")).toMatchObject({ count: 1, positiveCount: 1 });
    expect(result.find((item) => item.id === "memory_integrity")).toMatchObject({ count: 2, positiveCount: 1 });
    expect(result.find((item) => item.id === "unknown:future_detector")).toEqual({ id: "unknown:future_detector", label: "미등록 · future detector", count: 1, positiveCount: 1 });
    expect(result.reduce((sum, item) => sum + item.count, 0)).toBe(7);
  });

  it("deduplicates by Event ID before checking module or event kind, with last value winning", () => {
    const values = [
      event(1, { id: "changed", module: "esp", raw_score: 1 }),
      event(2, { id: "removed", module: "esp", raw_score: 7 }),
      event(3, { id: "changed", module: "noclip", raw_score: 3 }),
      event(4, { id: "removed", module: "esp", event_kind: "operational" }),
      event(3, { id: "changed", module: "noclip", raw_score: 3 }),
    ];
    expect(detectorObservationCounts(values)).toEqual([{ id: "noclip", label: "Noclip", count: 1, positiveCount: 1 }]);
  });

  it("sorts tied counts by label and then ID and does not mutate Events", () => {
    const values = [event(2, { module: "future_b" }), event(1, { module: "future_a" }), event(3, { module: "future a" })];
    const before = structuredClone(values);
    expect(detectorObservationCounts(values).map((item) => item.id)).toEqual(["unknown:future a", "unknown:future_a", "unknown:future_b"]);
    expect(values).toEqual(before);
    expect(detectorObservationCounts([...values].reverse())).toEqual(detectorObservationCounts(values));
  });
});

describe("one-session observation sequence flow", () => {
  it("returns no points when the session has no finite nonnegative integer-sequence detection", () => {
    expect(sessionObservationFlow([], "session-A")).toEqual([]);
    expect(sessionObservationFlow([event(1, { session_id: "session-B" }), event(2, { event_kind: "operational" }), event(Number.NaN), event(Number.POSITIVE_INFINITY), event(-1), event(1.5)], "session-A")).toEqual([]);
  });

  it("uses actual receiver sequence values rather than relative timestamps across sessions", () => {
    const values = [
      event(90, { timestamp_ms: 1, raw_score: 1 }),
      event(200, { session_id: "session-B", timestamp_ms: 5, raw_score: 7 }),
      event(4, { timestamp_ms: 99_000, raw_score: 0 }),
      event(17, { timestamp_ms: 50_000, raw_score: 3 }),
    ];
    expect(sessionObservationFlow(values, "session-A", 3)).toEqual([
      { sequenceStart: 4, sequenceEnd: 32, count: 2, positiveCount: 1 },
      { sequenceStart: 33, sequenceEnd: 61, count: 0, positiveCount: 0 },
      { sequenceStart: 62, sequenceEnd: 90, count: 1, positiveCount: 1 },
    ]);
    expect(sessionObservationFlow(values.map((item) => ({ ...item, timestamp_ms: 100_000 - item.timestamp_ms })), "session-A")).toEqual(sessionObservationFlow(values, "session-A"));
  });

  it("deduplicates before session and event-kind filters so moved or replaced IDs are not double-counted", () => {
    const values = [
      event(1, { id: "moved", raw_score: 1 }),
      event(2, { id: "kind-changed", raw_score: 1 }),
      event(3, { id: "kept", raw_score: 1 }),
      event(4, { id: "moved", session_id: "session-B" }),
      event(5, { id: "kind-changed", event_kind: "operational" }),
      event(9, { id: "kept", raw_score: 0 }),
    ];
    expect(sessionObservationFlow(values, "session-A")).toEqual([{ sequenceStart: 9, sequenceEnd: 9, count: 1, positiveCount: 0 }]);
  });

  it("caps fixed sequence ranges and preserves all count totals without mutation", () => {
    const values = [event(101, { raw_score: 7 }), event(3), event(22, { raw_score: 2 }), event(500), event(27, { raw_score: 3 })];
    const before = structuredClone(values);
    const result = sessionObservationFlow(values, "session-A", 2);
    expect(result).toEqual([
      { sequenceStart: 3, sequenceEnd: 251, count: 4, positiveCount: 3 },
      { sequenceStart: 252, sequenceEnd: 500, count: 1, positiveCount: 0 },
    ]);
    expect(result.reduce((sum, item) => sum + item.count, 0)).toBe(5);
    expect(result.reduce((sum, item) => sum + item.positiveCount, 0)).toBe(3);
    expect(values).toEqual(before);
    expect(sessionObservationFlow([...values].reverse(), "session-A", 2)).toEqual(result);
  });

  it("shows an empty ingest range instead of flattening density into equal observation counts", () => {
    const values = [1, 2, 3, 10, 11, 12].map((sequence) => event(sequence, { raw_score: sequence % 2 }));
    expect(sessionObservationFlow(values, "session-A", 3)).toEqual([
      { sequenceStart: 1, sequenceEnd: 4, count: 3, positiveCount: 2 },
      { sequenceStart: 5, sequenceEnd: 8, count: 0, positiveCount: 0 },
      { sequenceStart: 9, sequenceEnd: 12, count: 3, positiveCount: 1 },
    ]);
  });

  it("does not describe other-session ingest gaps as elapsed gameplay time", () => {
    const values = [event(0), event(1, { session_id: "session-B" }), event(2, { session_id: "session-B" }), event(3, { timestamp_ms: 1 })];
    expect(sessionObservationFlow(values, "session-A", 4)).toEqual([
      { sequenceStart: 0, sequenceEnd: 0, count: 1, positiveCount: 0 },
      { sequenceStart: 1, sequenceEnd: 1, count: 0, positiveCount: 0 },
      { sequenceStart: 2, sequenceEnd: 2, count: 0, positiveCount: 0 },
      { sequenceStart: 3, sequenceEnd: 3, count: 1, positiveCount: 0 },
    ]);
  });

  it("uses at most twelve bins by default and floors the requested point cap", () => {
    const values = Array.from({ length: 31 }, (_, index) => event(index * 7 + 1, { raw_score: index % 2 }));
    expect(sessionObservationFlow(values, "session-A")).toHaveLength(12);
    expect(sessionObservationFlow(values, "session-A").reduce((sum, item) => sum + item.count, 0)).toBe(31);
    expect(sessionObservationFlow(values.slice(0, 3), "session-A", 100)).toHaveLength(15);
    expect(sessionObservationFlow(values, "session-A", 2.9)).toHaveLength(2);
  });

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY, 0.5])("does not create bins for invalid limit %s", (limit) => {
    expect(sessionObservationFlow([event(1)], "session-A", limit)).toEqual([]);
  });
});
