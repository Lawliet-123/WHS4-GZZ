import { describe, expect, it } from "vitest";
import { demoEvents, demoSnapshots } from "./mockData";
import { policyEvidenceRows, representativeReason, sortModuleSignals } from "./policyEvidence";
import type { DashboardEvent, SnapshotResponse } from "./types";

function event(sequence: number, changes: Partial<DashboardEvent> = {}): DashboardEvent {
  return {
    ...demoEvents.items[0]!, id: `event-${sequence}`, sequence,
    session_id: "session", player_id: "player", module: "noclip", timestamp_ms: sequence * 1000,
    raw_score: 0, evidence: {}, reasons: [], ...changes,
  };
}

function state(item: DashboardEvent) {
  const { id, ...rest } = item;
  return { ...rest, event_id: id };
}

function snapshot(events: DashboardEvent[], explanations: unknown[] = []): SnapshotResponse {
  return {
    ...demoSnapshots["demo_normal_001::player_007"]!, session_id: "session", player_id: "player",
    modules: events.map(state), policy: { module_evidence: explanations }, final_verdict: null,
  };
}

function explanation(latest: DashboardEvent, status: string, retained: DashboardEvent | null = null, threshold: number | null = 3) {
  return {
    module: latest.module, submodule: latest.evidence.submodule ?? null,
    signal: { status, calibration_threshold: threshold, calibration_mode: "threshold" },
    latest_event: state(latest), retained_incident_event: retained ? state(retained) : null,
  };
}

describe("Scoring explanation projection", () => {
  it("does not infer ACTIVE or a threshold from raw score", () => {
    const observed = event(1, { raw_score: 100 });
    const row = policyEvidenceRows(snapshot([observed]), [observed])[0]!;
    expect(row.status).toBe("UNKNOWN");
    expect(row.authoritative).toBe(false);
    expect(row.threshold).toBeNull();
    expect(row.retained).toBeNull();
  });

  it("preserves current zero and the original retained DLL incident separately", () => {
    const incident = event(2, { module: "external_access", raw_score: 2, evidence: { submodule: "module_integrity" }, reasons: ["Unsigned added DLL"] });
    const latest = event(3, { module: "external_access", evidence: { submodule: "module_integrity", status: "NORMAL" } });
    const row = policyEvidenceRows(snapshot([latest], [explanation(latest, "ACTIVE", incident, 2)]), [event(1, { module: "external_access", evidence: { submodule: "module_integrity" } }), incident, latest])[0]!;
    expect(row.status).toBe("ACTIVE");
    expect(row.latest?.raw_score).toBe(0);
    expect(row.retained?.raw_score).toBe(2);
    expect(row.highestObserved?.id).toBe(incident.id);
    expect(representativeReason(row)).toBe("Unsigned added DLL");
  });

  it("does not retain a high observation when the server signal is INACTIVE", () => {
    const incident = event(1, { raw_score: 3 });
    const latest = event(2);
    const row = policyEvidenceRows(snapshot([latest], [explanation(latest, "INACTIVE", incident)]), [incident, latest])[0]!;
    expect(row.status).toBe("INACTIVE");
    expect(row.highestObserved?.raw_score).toBe(3);
    expect(row.retained).toBeNull();
  });

  it("keeps external process and DLL channels independent without an aggregate duplicate", () => {
    const dll = event(1, { module: "external_access", evidence: { submodule: "module_integrity" } });
    const process = event(2, { module: "external_access", raw_score: 3, evidence: { submodule: "external_process" } });
    const derived = event(3, { module: "external_access", raw_score: 3, evidence: { submodule: "aggregate", derived: true } });
    const rows = policyEvidenceRows(snapshot([derived], [explanation(dll, "INACTIVE", null, 2), explanation(process, "ACTIVE", null, 2)]), [dll, process]);
    expect(rows).toHaveLength(2);
    expect(rows.map((row) => row.submodule).sort()).toEqual(["external_process", "module_integrity"]);
    expect(rows.find((row) => row.submodule === "module_integrity")?.latest?.raw_score).toBe(0);
  });

  it("rejects cross-subject and cross-channel retained witnesses", () => {
    const latest = event(1, { module: "external_access", evidence: { submodule: "module_integrity" } });
    const wrongSubject = event(2, { ...latest, id: "other-subject", raw_score: 2, player_id: "other-player" });
    const wrongScope = event(3, { ...latest, id: "other-channel", raw_score: 2, evidence: { submodule: "external_process" } });
    for (const wrong of [wrongSubject, wrongScope]) {
      const row = policyEvidenceRows(snapshot([latest], [explanation(latest, "ACTIVE", wrong)]), [wrong])[0]!;
      expect(row.retained).toBeNull();
      expect(row.highestObserved).toBeNull();
    }
  });

  it("leaves null, absent, and malformed thresholds unprovided", () => {
    const latest = event(1);
    for (const value of [null, undefined, "3", false, NaN]) {
      const entry = explanation(latest, "INACTIVE");
      entry.signal.calibration_threshold = value as number;
      expect(policyEvidenceRows(snapshot([latest], [entry]), [latest])[0]!.threshold).toBeNull();
    }
  });

  it("redacts evidence-linked identifiers and paths from the representative reason", () => {
    const latest = event(1, {
      evidence: { username: "SensitiveUser", computer_name: "Machine-A", window_title: "Private chat" },
      reasons: ["SensitiveUser Machine-A Private chat C:\\Users\\SensitiveUser\\secret.dll"],
    });
    const row = policyEvidenceRows(snapshot([latest], [explanation(latest, "ACTIVE")]), [latest])[0]!;
    const reason = representativeReason(row);
    expect(reason).not.toMatch(/SensitiveUser|Machine-A|Private chat|C:\\/);
    expect(reason).toContain("[redacted]");
  });

  it("sorts observations independently of ACTIVE status, without changing inputs", () => {
    const highHistory = event(1, { module: "noclip", raw_score: 50 });
    const lowLatest = event(2, { module: "noclip", raw_score: 0 });
    const higherLatest = event(3, { module: "aimbot", raw_score: 4 });
    const rows = policyEvidenceRows(snapshot([lowLatest, higherLatest], [explanation(lowLatest, "INACTIVE"), explanation(higherLatest, "ACTIVE", null, 4)]), [highHistory, lowLatest, higherLatest]);
    expect(sortModuleSignals(rows, "latest")[0]!.module).toBe("aimbot");
    expect(sortModuleSignals(rows, "highest")[0]!.module).toBe("noclip");
    expect(rows.find((row) => row.module === "noclip")?.status).toBe("INACTIVE");
    expect(rows).toHaveLength(2);
  });

  it("uses gameplay time before arrival sequence for a historical-only latest observation", () => {
    const current = event(1, { timestamp_ms: 2000, raw_score: 0 });
    const lateOld = event(2, { timestamp_ms: 1000, raw_score: 3 });
    const row = policyEvidenceRows(snapshot([]), [current, lateOld])[0]!;
    expect(row.latest?.id).toBe(current.id);
    expect(row.highestObserved?.id).toBe(lateOld.id);
    expect(row.status).toBe("UNKNOWN");
  });

  it("does not use unrelated modules or subjects as witnesses", () => {
    const latest = event(1);
    const rows = policyEvidenceRows(snapshot([latest]), [event(2, { player_id: "other-player", raw_score: 999 })]);
    expect(rows).toHaveLength(1);
    expect(rows[0]!.highestObserved).toBeNull();
    expect(policyEvidenceRows(null, [latest])).toEqual([]);
  });

  it("preserves canonical unknown and null clocks instead of accepting Scoring-state overrides", () => {
    const latest = event(1, { time_basis: "unknown", observed_at_utc: null, received_at_utc: null });
    const entry = explanation(latest, "INACTIVE");
    Object.assign(entry.latest_event, {
      time_basis: "session_relative", observed_at_utc: "2026-10-07T10:00:00.957Z", received_at_utc: "2026-10-07T10:00:01.000Z",
    });
    const row = policyEvidenceRows(snapshot([latest], [entry]), [latest])[0]!;
    expect(row.latest?.time_basis).toBe("unknown");
    expect(row.latest?.observed_at_utc).toBeNull();
    expect(row.latest?.received_at_utc).toBeNull();
  });

  it("requires an original Event rather than deriving clocks from snapshot evidence or metadata", () => {
    const latest = event(1, {
      time_basis: "session_relative", observed_at_utc: "2026-10-07T10:00:00.957Z", received_at_utc: "2026-10-07T10:00:01.000Z",
      evidence: { time_basis: "session_relative", timestamp_basis: "launcher_session_start", session_start_unix_ms: 1791367718186 },
    });
    const row = policyEvidenceRows(snapshot([latest], [explanation(latest, "INACTIVE")]), [])[0]!;
    expect(row.latest?.timestamp_ms).toBe(latest.timestamp_ms);
    expect(row.latest?.time_basis).toBe("unknown");
    expect(row.latest?.observed_at_utc).toBeNull();
    expect(row.latest?.received_at_utc).toBeNull();
  });

  it("does not borrow canonical clocks when the original sequence or score conflicts", () => {
    const latest = event(1, { time_basis: "session_relative", observed_at_utc: "2026-10-07T10:00:00.957Z" });
    for (const change of [{ sequence: 2 }, { raw_score: 3 }]) {
      const row = policyEvidenceRows(snapshot([latest], [explanation(latest, "INACTIVE")]), [{ ...latest, ...change }])[0]!;
      expect(row.latest?.time_basis).toBe("unknown");
      expect(row.latest?.observed_at_utc).toBeNull();
    }
  });

  it("does not attach a contributor's clocks to a derived aggregate even when IDs and values coincide", () => {
    const original = event(1, {
      module: "external_access", evidence: { submodule: "aggregate" }, time_basis: "session_relative",
      observed_at_utc: "2026-10-07T10:00:00.957Z", received_at_utc: "2026-10-07T10:00:01.000Z",
    });
    const derived = { ...original, evidence: { ...original.evidence, derived: true } };
    const row = policyEvidenceRows(snapshot([derived]), [original])[0]!;
    expect(row.latest?.id).toBe(original.id);
    expect(row.latest?.sequence).toBe(original.sequence);
    expect(row.latest?.timestamp_ms).toBe(original.timestamp_ms);
    expect(row.latest?.raw_score).toBe(original.raw_score);
    expect(row.latest?.time_basis).toBe("unknown");
    expect(row.latest?.observed_at_utc).toBeNull();
    expect(row.latest?.received_at_utc).toBeNull();
    const ordinary = policyEvidenceRows(snapshot([original]), [original])[0]!;
    expect(ordinary.latest?.time_basis).toBe("session_relative");
    expect(ordinary.latest?.observed_at_utc).toBe(original.observed_at_utc);
    expect(ordinary.latest?.received_at_utc).toBe(original.received_at_utc);
  });
});
