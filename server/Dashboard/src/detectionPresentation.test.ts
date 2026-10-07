import { describe, expect, it } from "vitest";
import { connectedClientCount, detectionType, eventReportedStatus, eventVerdict, explicitSeverity, optionalEvidenceText, sortDetectionEvents } from "./detectionPresentation";
import { redactEventText } from "./components/EvidenceDrawer";
import { demoEvents, demoOverview } from "./mockData";
import type { DashboardEvent, LauncherOverviewStatus } from "./types";

const event = (changes: Partial<DashboardEvent> = {}): DashboardEvent => ({ ...demoEvents.items[0]!, ...changes });
const status = (changes: Partial<LauncherOverviewStatus> = {}): LauncherOverviewStatus => ({
  ...demoOverview.launcher_statuses[0]!,
  state: "healthy",
  connected: true,
  ...changes,
});

describe("explicit SOC detection presentation", () => {
  it.each([
    ["NORMAL", "NORMAL", "success"], [" suspicious ", "SUSPICIOUS", "warning"], ["error", "ERROR", "danger"],
    ["INSUFFICIENT", "INSUFFICIENT", "warning"], ["STOPPED", "STOPPED", "neutral"],
  ])("keeps the directly reported event status %s independent of raw score", (status, label, tone) => {
    expect(eventReportedStatus(event({ raw_score: 0, evidence: { status } }))).toEqual({ label, tone });
  });

  it.each([undefined, null, "", "constructor", "not-a-state", 0, { state: "NORMAL" }, ["ERROR"]])(
    "leaves event status %j unprovided rather than deriving it from scores or player verdicts",
    (status) => {
      for (const raw_score of [0, 999]) expect(eventReportedStatus(event({ raw_score, evidence: { status } }))).toEqual({ label: "미제공", tone: "neutral" });
    },
  );

  it("keeps NORMAL raw zero and the player's current SUSPICIOUS verdict in different presentations", () => {
    const assessment = demoOverview.assessments.find((item) => item.status === "SUSPICIOUS")!;
    const observed = event({ session_id: assessment.session_id, player_id: assessment.player_id, raw_score: 0, evidence: { status: "NORMAL" } });
    expect(eventReportedStatus(observed).label).toBe("NORMAL");
    expect(eventVerdict(observed, demoOverview.assessments)).toBe("SUSPICIOUS");
  });

  it.each([
    ["critical", "Critical"], ["HIGH", "High"], [" Medium ", "Medium"], ["LoW", "Low"],
  ])("canonicalizes explicit severity %s", (value, expected) => {
    expect(explicitSeverity(event({ evidence: { severity: value } }))).toBe(expected);
  });

  it.each([undefined, null, "", "informational", "urgent", 4, { level: "critical" }, ["high"]])(
    "does not invent a severity from %j or a high raw score",
    (severity) => expect(explicitSeverity(event({ raw_score: 999, evidence: { severity } }))).toBeNull(),
  );

  it("does not turn an authoritative suspicious verdict into a severity", () => {
    const assessment = demoOverview.assessments.find((item) => item.status === "SUSPICIOUS")!;
    const suspicious = event({ session_id: assessment.session_id, player_id: assessment.player_id, evidence: {} });
    expect(eventVerdict(suspicious, demoOverview.assessments)).toBe("SUSPICIOUS");
    expect(explicitSeverity(suspicious)).toBeNull();
  });

  it("uses only sanitized optional evidence strings and does not mutate the evidence", () => {
    const evidence = {
      username: "private-user",
      hostname: "private-host",
      window_title: "Private chat",
      full_path: "C:\\Users\\private-user\\capture.txt",
      event_type: "private-user / private-host / Private chat / C:\\Users\\private-user\\capture.txt",
      severity: "High",
      count: 12,
    };
    const original = structuredClone(evidence);
    const result = detectionType(event({ evidence }));
    for (const privateText of ["private-user", "private-host", "Private chat", "C:\\Users"]) expect(result).not.toContain(privateText);
    expect(result).toContain("[redacted]");
    expect(optionalEvidenceText(event({ evidence }), "username")).toBeNull();
    expect(optionalEvidenceText(event({ evidence }), "full_path")).toBeNull();
    expect(optionalEvidenceText(event({ evidence }), "count")).toBeNull();
    expect(optionalEvidenceText(event({ evidence }), "constructor")).toBeNull();
    expect(evidence).toEqual(original);
  });

  it("redacts plain private evidence values from free-form Event reasons and client text", () => {
    const value = event({ evidence: { username: "LAB-PRIVATE-USER", hostname: "LAB-PRIVATE-PC", window_title: "LAB PRIVATE WINDOW", source_image: "C:\\Users\\LAB-PRIVATE-USER\\tool.exe" } });
    const reason = "LAB-PRIVATE-USER observed LAB-PRIVATE-PC / LAB PRIVATE WINDOW at C:\\Users\\LAB-PRIVATE-USER\\tool.exe";
    const safe = redactEventText(value, reason);
    for (const privateValue of ["LAB-PRIVATE-USER", "LAB-PRIVATE-PC", "LAB PRIVATE WINDOW", "C:\\Users"]) expect(safe).not.toContain(privateValue);
    expect(safe).toContain("[redacted]");
    expect(redactEventText(value, "source id source-42 / severity high")).toBe("source id source-42 / severity high");
  });

  it("prefers explicit type, then distinguishes operational and detection fallbacks", () => {
    expect(detectionType(event({ event_kind: "operational", evidence: { event_type: "Heartbeat timeout" } }))).toBe("Heartbeat timeout");
    expect(detectionType(event({ event_kind: "operational", evidence: {} }))).toBe("운영 상태");
    expect(detectionType(event({ module: "external_access", evidence: { submodule: "module_integrity" } }))).toBe("DLL 무결성");
    expect(detectionType(event({ module: "esp", evidence: { event_type: "   ", submodule: null } }))).toBe("ESP 접근 감시 관측");
    expect(detectionType(event({ module: "noclip", evidence: { event_type: { name: "unsafe object" }, submodule: 7 } }))).toBe("이동·충돌 관측");
  });

  it("does not treat prototype property names as renderable module labels", () => {
    expect(typeof detectionType(event({ evidence: { submodule: "constructor" } }))).toBe("string");
    expect(typeof detectionType(event({ module: "__proto__", evidence: {} }))).toBe("string");
    expect(explicitSeverity(event({ evidence: { severity: "constructor" } }))).toBeNull();
  });

  it("matches verdicts by both session and player, not by raw score or a different session", () => {
    const known = demoOverview.assessments[0]!;
    expect(eventVerdict(event({ session_id: known.session_id, player_id: known.player_id }), demoOverview.assessments)).toBe(known.status);
    expect(eventVerdict(event({ session_id: "other-session", player_id: known.player_id, raw_score: 999 }), demoOverview.assessments)).toBe("UNKNOWN");
    expect(eventVerdict(event({ session_id: known.session_id, player_id: "other-player" }), demoOverview.assessments)).toBe("UNKNOWN");
  });
});

describe("distinct connected clients", () => {
  const withClient = (clientId: string, changes: Partial<LauncherOverviewStatus> = {}): LauncherOverviewStatus => {
    const base = status();
    return { ...base, source: { ...base.source!, client_id: clientId }, ...changes };
  };

  it("leaves a missing client identity unprovided instead of using player counts", () => {
    expect(connectedClientCount([])).toBeNull();
    expect(connectedClientCount([status({ source: null })])).toBeNull();
    expect(connectedClientCount([withClient("   ")])).toBeNull();
  });

  it("deduplicates a client across sessions and counts only connected healthy or online clients", () => {
    const values = [
      withClient("client-A"),
      withClient("client-A", { session_id: "other-session", player_id: "other-player" }),
      withClient("client-B", { state: "online" }),
      withClient("client-C", { state: "stopped" }),
      withClient("client-D", { state: "failed" }),
      withClient("client-E", { connected: false }),
      withClient("client-F", { state: "stale" }),
      withClient("client-G", { state: "degraded" }),
      withClient("client-H", { state: "starting" }),
    ];
    const original = structuredClone(values);
    expect(connectedClientCount(values)).toBe(2);
    expect(values).toEqual(original);
    expect(connectedClientCount([withClient("known-offline", { connected: false })])).toBe(0);
  });
});

describe("detection sorting", () => {
  const ids = (events: DashboardEvent[]) => events.map((item) => item.id);

  it("sorts receiver ingest sequence rather than relative gameplay time across sessions", () => {
    const values = [event({ id: "new", sequence: 5, timestamp_ms: 100, session_id: "new-session" }), event({ id: "old", sequence: 2, timestamp_ms: 999999, session_id: "old-session" })];
    expect(ids(sortDetectionEvents(values, "sequence", "desc"))).toEqual(["new", "old"]);
    expect(ids(sortDetectionEvents(values, "sequence", "asc"))).toEqual(["old", "new"]);
  });

  it("sorts raw scores in either direction with deterministic newest-sequence ties and no mutation", () => {
    const values = [event({ id: "high-old", sequence: 1, raw_score: 3 }), event({ id: "low", sequence: 3, raw_score: 1 }), event({ id: "high-new", sequence: 5, raw_score: 3 })];
    const original = [...values];
    expect(ids(sortDetectionEvents(values, "score", "asc"))).toEqual(["low", "high-new", "high-old"]);
    expect(ids(sortDetectionEvents(values, "score", "desc"))).toEqual(["high-new", "high-old", "low"]);
    expect(values).toEqual(original);
    expect(sortDetectionEvents(values, "score", "desc")[0]).toBe(values[2]);
  });

  it("keeps unknown severity last even when sorting ascending", () => {
    const values = [
      event({ id: "missing-old", sequence: 1, evidence: {}, raw_score: 999 }),
      event({ id: "low", sequence: 2, evidence: { severity: "low" } }),
      event({ id: "critical", sequence: 3, evidence: { severity: "critical" }, raw_score: 0 }),
      event({ id: "high", sequence: 4, evidence: { severity: "HIGH" } }),
      event({ id: "medium", sequence: 5, evidence: { severity: "Medium" } }),
      event({ id: "invalid-new", sequence: 6, evidence: { severity: "urgent" } }),
    ];
    expect(ids(sortDetectionEvents(values, "severity", "asc"))).toEqual(["low", "medium", "high", "critical", "invalid-new", "missing-old"]);
    expect(ids(sortDetectionEvents(values, "severity", "desc"))).toEqual(["critical", "high", "medium", "low", "invalid-new", "missing-old"]);
  });

  it("preserves source order if both sort value and sequence are identical", () => {
    const values = [event({ id: "first", sequence: 5, raw_score: 1, evidence: { severity: "low" } }), event({ id: "second", sequence: 5, raw_score: 1, evidence: { severity: "low" } })];
    for (const key of ["sequence", "score", "severity"] as const) {
      expect(ids(sortDetectionEvents(values, key, "desc"))).toEqual(["first", "second"]);
      expect(ids(sortDetectionEvents(values, key, "asc"))).toEqual(["first", "second"]);
    }
  });
});
