import { describe, expect, it } from "vitest";
import { demoEvents, demoOverview, demoSnapshots } from "./mockData";
import { policyEvidenceRows } from "./policyEvidence";

describe("explicit synthetic Scoring explanations", () => {
  it("projects distinct synthetic SelfDefense functions from their original operational events", () => {
    const rows = demoOverview.selfdefense_statuses!;
    expect(rows.map((item) => item.kind)).toEqual(["module_health", "file_integrity", "debugger_presence"]);
    expect(rows.find((item) => item.kind === "module_health")).toMatchObject({ target_module: "esp", status: "NORMAL", scan_complete: true });
    expect(rows.find((item) => item.kind === "file_integrity")).toMatchObject({ status: "ERROR", raw_score: 0, scan_complete: false });
    expect(rows.find((item) => item.kind === "debugger_presence")).toMatchObject({ status: "DETECTED", raw_score: 0 });
    for (const row of rows) {
      const event = demoEvents.items.find((item) => item.id === row.event_id)!;
      expect(event.event_kind).toBe("operational");
      expect(event.evidence.synthetic).toBe(true);
      expect(row.evidence).toBe(event.evidence);
      expect(row).toMatchObject({ kind: event.evidence.kind, status: event.evidence.status, scan_complete: event.evidence.scan_complete, sequence: event.sequence, raw_score: event.raw_score });
    }
    expect(demoOverview.counts.operational_events).toBe(rows.length);
    expect(demoSnapshots["demo_esp_001::player_042"]!.final_verdict!.active_modules).not.toContain("selfdefense");
  });
  it("provides server-owned-looking explanation envelopes without publishing player scores", () => {
    for (const snapshot of Object.values(demoSnapshots)) {
      expect(snapshot.policy.synthetic).toBe(true);
      expect(snapshot.score).toBeNull();
      expect(snapshot.confidence).toBeNull();
      expect(snapshot.policy.aggregate_risk).toMatchObject({
        session_id: snapshot.session_id, player_id: snapshot.player_id,
        active_module_count: snapshot.final_verdict!.active_module_count,
        evidence_unit_count: snapshot.final_verdict!.evidence_unit_count,
      });
      expect(Array.isArray(snapshot.policy.module_evidence)).toBe(true);
    }
  });

  it("keeps pending ESP distinct from active external access and operational SelfDefense", () => {
    const snapshot = demoSnapshots["demo_esp_001::player_042"]!;
    const rows = policyEvidenceRows(snapshot, demoEvents.items);
    expect(rows.find((row) => row.submodule === "external_process")).toMatchObject({ status: "ACTIVE", threshold: 2 });
    expect(rows.find((row) => row.submodule === "module_integrity")).toMatchObject({ status: "INACTIVE", threshold: 2 });
    expect(rows.find((row) => row.module === "esp")).toMatchObject({ status: "UNRESOLVED", threshold: null });
    expect(rows.find((row) => row.module === "selfdefense")?.status).toBe("UNKNOWN");
    expect(snapshot.final_verdict!.unresolved_modules).toEqual(["esp"]);
    expect(rows.filter((row) => row.status === "ACTIVE")).toHaveLength(1);
  });

  it("does not fabricate retained incidents for these fixed current-state scenarios", () => {
    for (const snapshot of Object.values(demoSnapshots)) {
      expect(policyEvidenceRows(snapshot, demoEvents.items).every((row) => row.retained === null)).toBe(true);
    }
  });

  it("labels synthetic clock metadata explicitly rather than deriving it at render time", () => {
    expect(demoEvents.items.every((event) => event.time_basis === "session_relative")).toBe(true);
    expect(demoEvents.items.every((event) => event.received_at_utc === "2026-10-05T03:20:00+00:00")).toBe(true);
    for (const event of demoEvents.items) {
      expect(event.evidence.timestamp_basis).toBe("launcher_session_start");
      expect(event.observed_at_utc).toBe(new Date(
        Number(event.evidence.session_start_unix_ms) + event.timestamp_ms,
      ).toISOString());
    }
  });
});
