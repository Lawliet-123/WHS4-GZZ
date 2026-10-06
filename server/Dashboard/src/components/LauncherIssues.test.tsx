// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoOverview } from "../mockData";
import type { LauncherOverviewStatus, StatusComponent } from "../types";
import { LauncherIssues } from "./LauncherIssues";

afterEach(cleanup);

function launcher(components: StatusComponent[], changes: Partial<LauncherOverviewStatus> = {}): LauncherOverviewStatus {
  return {
    ...demoOverview.launcher_statuses[0]!, session_id: "session_warning", player_id: "player_7", state: "degraded",
    source: { ...demoOverview.launcher_statuses[0]!.source!, client_id: "launcher-12345", components },
    ...changes,
  };
}

function component(id: string, raw: "WARN" | "SKIPPED" | "STOPPED" | "FAILED"): StatusComponent {
  return {
    ...demoOverview.launcher_statuses[0]!.source!.components[0]!, id,
    state: raw === "FAILED" ? "failed" : raw === "WARN" ? "degraded" : "stopped",
    reported_status: raw === "FAILED" ? "failed" : raw === "WARN" ? "degraded" : "stopped",
    details: { launcher_status: raw, mode: "continuous" },
  };
}

describe("LauncherIssues", () => {
  it("shows exact affected subject and client, and drills down to that subject", () => {
    const onSubject = vi.fn();
    render(<LauncherIssues launcherStatuses={[launcher([component("esp", "WARN"), component("noclip", "STOPPED")])]} moduleStatuses={[]} onSubject={onSubject} />);
    const table = screen.getByRole("table");
    expect(within(table).getByText("player_7")).toBeTruthy();
    expect(within(table).getByText("launcher-12345")).toBeTruthy();
    expect(within(table).getByText("WARN")).toBeTruthy();
    expect(within(table).queryByText("STOPPED")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "session_warning player_7 상세 보기" }));
    expect(onSubject).toHaveBeenCalledExactlyOnceWith("session_warning", "player_7");
  });

  it("shows SKIPPED as skipped with a stopped effective state, not as failed", () => {
    render(<LauncherIssues launcherStatuses={[launcher([component("hide_anywhere", "SKIPPED")])]} moduleStatuses={[]} />);
    expect(screen.getByText("건너뜀")).toBeTruthy();
    expect(screen.getByText("중지")).toBeTruthy();
    expect(screen.getByText("SKIPPED")).toBeTruthy();
    expect(screen.queryByText("실패")).toBeNull();
  });

  it("shows missing reports rather than a healthy empty state", () => {
    render(<LauncherIssues launcherStatuses={[launcher([], { source: null, state: "unknown", reason: "no Launcher heartbeat" })]} moduleStatuses={[]} />);
    expect(screen.getAllByText("미보고")).toHaveLength(2);
    expect(screen.getByText("확인 불가")).toBeTruthy();
    expect(screen.queryByText("점검 대상 없음")).toBeNull();
  });

  it("renders a compact empty state without fabricating missing modules", () => {
    render(<LauncherIssues launcherStatuses={[]} moduleStatuses={[]} />);
    expect(screen.getByText("점검 대상 없음")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("renders untrusted reason text literally and removes identifying values", () => {
    const failed = component("esp", "FAILED");
    failed.details = { launcher_status: "FAILED", username: "PrivateName", reason: "PrivateName <script>alert(1)</script> C:\\Users\\PrivateName\\logs" };
    const { container } = render(<LauncherIssues launcherStatuses={[launcher([failed])]} moduleStatuses={[]} />);
    expect(container.textContent).not.toContain("PrivateName");
    expect(container.textContent).toContain("<script>alert(1)</script>");
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("[redacted-path]");
  });
});
