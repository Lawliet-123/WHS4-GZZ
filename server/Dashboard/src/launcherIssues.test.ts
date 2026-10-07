import { describe, expect, it } from "vitest";
import { demoOverview } from "./mockData";
import { launcherIssues } from "./launcherIssues";
import type { ComponentStatus, LauncherOverviewStatus, ModuleStatus, StatusComponent } from "./types";

function component(id: string, state: ComponentStatus, launcherStatus: string | null = null): StatusComponent {
  return {
    id, state, reported_status: state, required: true, pid: 123,
    updated_at_ms: 200, stale_after_ms: 30_000, age_ms: 100, effective_age_ms: 100,
    details: launcherStatus ? { launcher_status: launcherStatus } : {},
  };
}

function launcher(components: StatusComponent[], changes: Partial<LauncherOverviewStatus> = {}): LauncherOverviewStatus {
  return {
    ...demoOverview.launcher_statuses[0]!, session_id: "session_a", player_id: "player_a",
    state: "healthy", connected: true, reason: "",
    source: {
      ...demoOverview.launcher_statuses[0]!.source!, client_id: "launcher-1000", state: "healthy", reported_status: "healthy",
      components, transport: { configured: true, consecutive_failures: 0, last_success_sequence: 1, last_error_type: null },
    },
    ...changes,
  };
}

function moduleStatus(item: StatusComponent, changes: Partial<ModuleStatus> = {}): ModuleStatus {
  return {
    ...item, label: item.id, status_id: `session_a:player_a:launcher-1000:${item.id}`,
    session_id: "session_a", player_id: "player_a", client_id: "launcher-1000", last_seen_at: "2026-10-07T00:00:00Z",
    ...changes,
  };
}

describe("launcherIssues", () => {
  it("retains failed, WARN and SKIPPED but excludes ordinary stopped and successful oneshot checks", () => {
    const status = launcher([
      component("esp", "failed", "FAILED"), component("kernel_watcher", "degraded", "WARN"),
      { ...component("hide_anywhere", "stopped", "SKIPPED"), required: false },
      component("noclip", "stopped", "STOPPED"), component("whistle_spoofing", "stopped", "DONE"),
      component("external_access", "running", "RUNNING"),
    ], { state: "degraded" });
    const rows = launcherIssues([status], []);
    expect(rows.map((row) => [row.componentId, row.kind, row.state])).toEqual([
      ["esp", "failed", "failed"], ["kernel_watcher", "warn", "degraded"], ["hide_anywhere", "skipped", "stopped"],
    ]);
    expect(rows.find((row) => row.kind === "skipped")!.required).toBe(false);
  });

  it("preserves stopped WARN as a last check rather than changing its current state to failed", () => {
    const rows = launcherIssues([launcher([component("memory_integrity", "stopped", "WARN")], { state: "stopped", connected: false })], []);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ state: "stopped", reportedStatus: "stopped", kind: "warn", launcherStatus: "WARN" });
  });

  it("deduplicates only exact subject, client and component identities", () => {
    const failed = component("esp", "failed", "FAILED");
    const a = launcher([failed], { state: "degraded" });
    const b = launcher([{ ...failed, state: "degraded", details: { launcher_status: "WARN" } }], { session_id: "session_b", state: "degraded" });
    const rows = launcherIssues([a, b], [
      moduleStatus(failed), moduleStatus(failed, { session_id: "session_b" }),
      moduleStatus(failed, { player_id: "other_player" }),
    ]);
    expect(rows).toHaveLength(3);
    expect(rows.filter((row) => row.sessionId === "session_a" && row.playerId === "player_a")).toHaveLength(1);
    expect(rows.find((row) => row.sessionId === "session_b")!.kind).toBe("warn");
    expect(rows.find((row) => row.playerId === "other_player")!.kind).toBe("failed");
  });

  it("does not borrow an old execution's failure for the selected current Launcher", () => {
    const current = launcher([component("esp", "running", "RUNNING")]);
    const oldModule = moduleStatus(component("esp", "failed", "FAILED"), { client_id: "launcher-999" });
    expect(launcherIssues([current], [oldModule])).toEqual([]);
  });

  it("fills a missing component from the module API only for that same reporting client", () => {
    const current = launcher([], { state: "degraded" });
    const rows = launcherIssues([current], [moduleStatus(component("esp", "degraded", "WARN"))]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ clientId: "launcher-1000", componentId: "esp", kind: "warn" });
  });

  it("distinguishes no report, unavailable storage and an ambiguous unknown source", () => {
    const statuses = [
      launcher([], { session_id: "unreported", state: "unknown", source: null, reason: "no Launcher heartbeat" }),
      launcher([], { session_id: "unavailable", state: "unknown", source: null, reason: "heartbeat storage not connected" }),
      launcher([], { session_id: "unknown", state: "unknown", source: null, reason: "source list truncated" }),
    ];
    const rows = launcherIssues(statuses, []);
    expect(rows.find((row) => row.sessionId === "unreported")!.kind).toBe("not_reported");
    expect(rows.find((row) => row.sessionId === "unavailable")!).toMatchObject({ kind: "unavailable", state: "unknown" });
    expect(rows.find((row) => row.sessionId === "unknown")!.kind).toBe("unknown");
    expect(rows.every((row) => row.clientId === null && row.reportedStatus === null)).toBe(true);
  });

  it("keeps stale source and module effective states separate from the last successful report", () => {
    const esp = { ...component("esp", "stale", "RUNNING"), reported_status: "running" as const };
    const status = launcher([esp], { state: "stale", connected: false });
    const rows = launcherIssues([status], []);
    expect(rows).toHaveLength(2);
    expect(rows.find((row) => row.componentId === "esp")).toMatchObject({ kind: "stale", state: "stale", reportedStatus: "running", launcherStatus: "RUNNING" });
    expect(rows.find((row) => row.componentId === "launcher-source")).toMatchObject({ kind: "stale", reportedStatus: "healthy" });
  });

  it("shows an unexplained Launcher degradation even when its modules have no reported fault", () => {
    const rows = launcherIssues([launcher([component("esp", "running")], { state: "degraded", reason: "Launcher loop delayed" })], []);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ componentId: "launcher-source", kind: "degraded", reason: "Launcher loop delayed" });
  });

  it("reports known transport failures without inferring a failed process", () => {
    const status = launcher([component("esp", "running")]);
    status.source!.transport = { configured: true, consecutive_failures: 2, last_success_sequence: 10, last_error_type: "TimeoutError" };
    const rows = launcherIssues([status], []);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ kind: "transport", state: "healthy", reason: "TimeoutError", details: { consecutive_failures: 2 } });
  });

  it("redacts local paths and identifying strings, and does not expose arbitrary detail fields", () => {
    const failed = component("esp", "failed", "FAILED");
    failed.details = {
      launcher_status: "FAILED", username: "secret-person", computer_name: "secret-machine",
      token: "never-render-this", mode: "continuous", last_code: 2,
      reason: "secret-person on secret-machine could not read C:\\Users\\secret-person\\private.dat",
    };
    const row = launcherIssues([launcher([failed])], [])[0]!;
    expect(JSON.stringify(row)).not.toMatch(/secret-person|secret-machine|never-render-this|private\.dat/);
    expect(row.reason).toContain("[redacted]");
    expect(row.reason).toContain("[redacted-path]");
    expect(row.details).toEqual({ mode: "continuous", last_code: 2 });
  });
});
