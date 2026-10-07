import { describe, expect, it } from "vitest";
import {
  buildModuleRollups,
  eventBelongsToModule,
  moduleFilterOptions,
  protectionModuleCatalog,
  resolveEventModule,
} from "./moduleCatalog";
import type { DashboardEvent, ModuleStatus } from "./types";

function event(
  sequence: number,
  module: string,
  evidence: Record<string, unknown> = {},
  eventKind: DashboardEvent["event_kind"] = "detection",
  playerId = "player_042",
): DashboardEvent {
  return {
    id: `event-${sequence}`,
    sequence,
    session_id: "session_001",
    player_id: playerId,
    module,
    timestamp_ms: sequence * 1_000,
    evidence,
    reasons: [],
    raw_score: eventKind === "detection" ? 1 : 0,
    event_kind: eventKind,
    time_basis: "unknown",
    evidence_image: null,
    log_excerpt: null,
  };
}

function component(id: string, state: ModuleStatus["state"], playerId = "player_042"): ModuleStatus {
  return {
    id,
    label: id,
    status_id: `session_001:${playerId}:launcher-1:${id}`,
    session_id: "session_001",
    player_id: playerId,
    client_id: "launcher-1",
    state,
    reported_status: state,
    required: true,
    pid: 100,
    updated_at_ms: 1_000,
    stale_after_ms: 30_000,
    age_ms: 100,
    effective_age_ms: 100,
    last_seen_at: "2026-10-05T00:00:00+00:00",
    details: {},
  };
}

describe("protection module catalog", () => {
  it("groups Watchdog, Integrity and AntiDebug runtime states without hiding failed checks", () => {
    const rollup = buildModuleRollups([], [component("self_defense", "healthy"), component("selfdefense_integrity", "failed"), component("selfdefense_anti_debug", "running")]).find((item) => item.id === "self_defense")!;
    expect(rollup.components).toHaveLength(3);
    expect(rollup.state).toBe("failed");
  });
  it("contains each of the 13 Launcher services exactly once", () => {
    expect(protectionModuleCatalog.map((entry) => entry.id)).toEqual([
      "self_defense",
      "kernel_watcher",
      "external_access",
      "module_integrity",
      "input_signature",
      "memory_integrity",
      "whistle_spoofing",
      "aimbot",
      "esp",
      "godmode",
      "noclip",
      "autopaint",
      "hide_anywhere",
    ]);
    expect(new Set(protectionModuleCatalog.map((entry) => entry.id)).size).toBe(13);
  });

  it("resolves real Shared module aliases and the external_access submodules", () => {
    expect(resolveEventModule(event(1, "external_access", { submodule: "module_integrity" })).id)
      .toBe("module_integrity");
    expect(resolveEventModule(event(2, "external_access", { submodule: "external_process" })).id)
      .toBe("external_access");
    expect(resolveEventModule(event(3, "localguard_yara")).id).toBe("input_signature");
    expect(resolveEventModule(event(4, "overlay_hook")).id).toBe("memory_integrity");
    expect(resolveEventModule(event(5, "whistle_rpc")).id).toBe("whistle_spoofing");
    expect(resolveEventModule(event(6, "selfdefense", {}, "operational")).id).toBe("self_defense");
    expect(resolveEventModule(event(7, "kernel_sentinel")).id).toBe("kernel_watcher");
  });

  it("keeps unmapped Event modules visible and filterable", () => {
    const future = event(1, "future_detector");
    const resolved = resolveEventModule(future);
    expect(resolved).toMatchObject({
      id: "unknown:future_detector",
      group: "unmapped",
      known: false,
      rawModule: "future_detector",
    });
    expect(moduleFilterOptions([future]).at(-1)).toEqual({
      value: "unknown:future_detector",
      label: "미등록 · future detector",
      group: "unmapped",
      known: false,
    });
    expect(eventBelongsToModule(future, "unknown:future_detector")).toBe(true);
  });

  it("builds complete service rollups without counting Launcher as a detector", () => {
    const events = [
      event(1, "whistle", {}, "detection"),
      event(2, "whistle_rpc", {}, "detection"),
      event(3, "selfdefense", {}, "operational"),
      event(4, "future_detector", {}, "detection", "player_007"),
    ];
    const statuses = [
      component("whistle_spoofing", "healthy"),
      component("whistle_spoofing", "stale", "player_007"),
      component("input_signature", "running"),
      component("future_component", "failed"),
      component("launcher", "failed"),
    ];

    const rollups = buildModuleRollups(events, statuses);
    const whistle = rollups.find((item) => item.id === "whistle_spoofing");
    const selfDefense = rollups.find((item) => item.id === "self_defense");
    const untouched = rollups.find((item) => item.id === "autopaint");

    expect(rollups.filter((item) => item.known)).toHaveLength(13);
    expect(rollups.some((item) => item.id === "unknown:launcher")).toBe(false);
    expect(whistle).toMatchObject({
      state: "stale",
      eventCount: 2,
      detectionCount: 2,
      operationalCount: 0,
      subjectCount: 1,
      latestEventAt: 2_000,
    });
    expect(whistle?.components).toHaveLength(2);
    expect(selfDefense).toMatchObject({ eventCount: 1, detectionCount: 0, operationalCount: 1 });
    expect(untouched).toMatchObject({ state: "not_reported", eventCount: 0, subjectCount: 0 });
    expect(rollups.find((item) => item.id === "unknown:future_detector"))
      .toMatchObject({ known: false, eventCount: 1, detectionCount: 1 });
    expect(rollups.find((item) => item.id === "unknown:future_component"))
      .toMatchObject({ known: false, state: "failed", eventCount: 0 });
  });
});
