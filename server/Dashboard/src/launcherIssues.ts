import { redactSensitiveText, sanitizeEvidence } from "./components/EvidenceDrawer";
import { humanizeModule } from "./domain";
import type { ComponentStatus, ConnectionState, LauncherOverviewStatus, ModuleStatus, StatusComponent } from "./types";

export type LauncherIssueKind = "failed" | "warn" | "skipped" | "missing" | "restarting"
  | "degraded" | "stale" | "unknown" | "unavailable" | "not_reported" | "transport";

export interface LauncherIssue {
  key: string;
  sessionId: string;
  playerId: string;
  clientId: string | null;
  componentId: string;
  label: string;
  kind: LauncherIssueKind;
  /** Effective heartbeat state is retained even when the last check was WARN. */
  state: ComponentStatus | ConnectionState;
  reportedStatus: ComponentStatus | null;
  launcherStatus: string | null;
  required: boolean | null;
  reason: string;
  details: Record<string, string | number | boolean>;
  sourceKind: "launcher" | "component" | "transport";
}

const attentionStates = new Set(["failed", "degraded", "stale", "unknown", "unavailable"]);
const launcherStates = new Set(["MISSING", "SKIPPED", "PENDING", "RUNNING", "DONE", "WARN", "RESTART", "FAILED", "STOPPED"]);
const detailKeys = ["mode", "runs", "restarts", "last_code", "phase", "loop_age_ms", "started_by"];
const issueOrder: Record<LauncherIssueKind, number> = {
  failed: 0, unavailable: 1, warn: 2, degraded: 3, transport: 4, stale: 5,
  restarting: 6, skipped: 7, missing: 8, unknown: 9, not_reported: 10,
};

/** All joins use the exact subject and reporting client, never a global module ID. */
const componentKey = (sessionId: string, playerId: string, clientId: string | null, componentId: string) => (
  JSON.stringify([sessionId, playerId, clientId, componentId])
);

function safePresentation(details: Record<string, unknown>, fallbackReason = "") {
  // Evidence-aware redaction also removes a name echoed in a free-form reason.
  const safe = sanitizeEvidence({ ...details, issue_reason: fallbackReason }).evidence;
  const result: Record<string, string | number | boolean> = {};
  for (const key of detailKeys) {
    const value = safe[key];
    if (typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value))) result[key] = value;
  }
  const explicitReason = ["reason", "failure_reason", "error_type", "error_message", "note", "issue_reason"]
    .map((key) => safe[key]).find((value) => typeof value === "string" && value.trim());
  const raw = safe.launcher_status;
  const launcherStatus = typeof raw === "string" && launcherStates.has(raw) ? raw : null;
  return { details: result, reason: typeof explicitReason === "string" ? explicitReason : "", launcherStatus };
}

function componentIssue(component: StatusComponent, sessionId: string, playerId: string, clientId: string): LauncherIssue | null {
  const presentation = safePresentation(component.details);
  const raw = presentation.launcherStatus;
  // SKIPPED is intentionally normalized to stopped; WARN may be stopped after
  // orderly Launcher cleanup. Show both fields, rather than changing the state.
  const explicitKind: LauncherIssueKind | null = raw === "FAILED" ? "failed" : raw === "WARN" ? "warn"
    : raw === "SKIPPED" ? "skipped" : raw === "MISSING" ? "missing" : raw === "RESTART" ? "restarting" : null;
  const kind = explicitKind ?? (attentionStates.has(component.state) ? component.state as LauncherIssueKind : null);
  if (!kind) return null;
  return {
    key: componentKey(sessionId, playerId, clientId, component.id), sessionId, playerId, clientId,
    componentId: component.id, label: redactSensitiveText(component.id === "launcher" ? "Launcher" : humanizeModule(component.id)),
    kind, state: component.state, reportedStatus: component.reported_status,
    launcherStatus: raw, required: component.required, sourceKind: component.id === "launcher" ? "launcher" : "component",
    reason: presentation.reason, details: presentation.details,
  };
}

function absentSourceKind(status: LauncherOverviewStatus): LauncherIssueKind {
  if (status.state === "unavailable" || /storage not connected/i.test(status.reason)) return "unavailable";
  if (status.state === "unknown" && /no Launcher heartbeat/i.test(status.reason)) return "not_reported";
  return attentionStates.has(status.state) ? status.state as LauncherIssueKind : "unknown";
}

/** Operational attention rows. These do not change or infer a player verdict. */
export function launcherIssues(
  launcherStatuses: readonly LauncherOverviewStatus[],
  moduleStatuses: readonly ModuleStatus[],
): LauncherIssue[] {
  const rows = new Map<string, LauncherIssue>();
  const subjects = new Set(launcherStatuses.map((status) => JSON.stringify([status.session_id, status.player_id])));
  for (const status of launcherStatuses) {
    const source = status.source;
    const clientId = source?.client_id ?? null;
    if (!source) {
      const kind = absentSourceKind(status);
      const key = componentKey(status.session_id, status.player_id, null, "launcher");
      rows.set(key, {
        key, sessionId: status.session_id, playerId: status.player_id, clientId: null,
        componentId: "launcher", label: "Launcher", kind, state: status.state,
        reportedStatus: null, launcherStatus: null, required: null, sourceKind: "launcher",
        reason: redactSensitiveText(status.reason), details: {},
      });
      continue;
    }
    // Overview.module_statuses repeats source.components. Deduplicate only the
    // same subject/client/component; old executions must not overwrite this one.
    const components = new Map(source.components.map((component) => [component.id, component]));
    for (const component of moduleStatuses) {
      if (component.session_id === status.session_id && component.player_id === status.player_id
        && component.client_id === clientId && !components.has(component.id)) components.set(component.id, component);
    }
    let componentCount = 0;
    for (const component of components.values()) {
      const row = componentIssue(component, status.session_id, status.player_id, source.client_id);
      if (row) { rows.set(row.key, row); componentCount += 1; }
    }
    if (attentionStates.has(status.state) && (status.state !== "degraded" || componentCount === 0)) {
      const key = componentKey(status.session_id, status.player_id, clientId, "launcher-source");
      const presentation = safePresentation({}, status.reason);
      rows.set(key, {
        key, sessionId: status.session_id, playerId: status.player_id, clientId,
        componentId: "launcher-source", label: "Launcher 연결", kind: status.state as LauncherIssueKind,
        state: status.state, reportedStatus: source.reported_status, launcherStatus: null,
        required: null, sourceKind: "launcher", reason: presentation.reason, details: {},
      });
    }
    if (source.transport.consecutive_failures > 0) {
      const key = componentKey(status.session_id, status.player_id, clientId, "launcher-transport");
      rows.set(key, {
        key, sessionId: status.session_id, playerId: status.player_id, clientId,
        componentId: "launcher-transport", label: "Launcher 중앙 전송", kind: "transport",
        state: source.state, reportedStatus: source.reported_status, launcherStatus: null,
        required: null, sourceKind: "transport", reason: redactSensitiveText(source.transport.last_error_type ?? ""),
        details: { consecutive_failures: source.transport.consecutive_failures },
      });
    }
  }
  // Component-only API data is useful when no Launcher row exists. Do not attach
  // another client's component to a selected current Launcher for that subject.
  for (const component of moduleStatuses) {
    if (subjects.has(JSON.stringify([component.session_id, component.player_id]))) continue;
    const row = componentIssue(component, component.session_id, component.player_id, component.client_id);
    if (row && !rows.has(row.key)) rows.set(row.key, row);
  }
  return [...rows.values()].sort((a, b) => issueOrder[a.kind] - issueOrder[b.kind]
    || a.sessionId.localeCompare(b.sessionId) || a.playerId.localeCompare(b.playerId)
    || (a.clientId ?? "").localeCompare(b.clientId ?? "") || a.componentId.localeCompare(b.componentId));
}

export function launcherIssueMeta(kind: LauncherIssueKind): { label: string; tone: string } {
  const labels: Record<LauncherIssueKind, { label: string; tone: string }> = {
    failed: { label: "실패", tone: "danger" }, warn: { label: "검사 경고", tone: "warning" },
    skipped: { label: "건너뜀", tone: "warning" }, missing: { label: "파일 없음", tone: "warning" },
    restarting: { label: "재시작", tone: "warning" }, degraded: { label: "일부 저하", tone: "warning" },
    stale: { label: "응답 지연", tone: "warning" }, unknown: { label: "확인 불가", tone: "neutral" },
    unavailable: { label: "사용 불가", tone: "warning" }, not_reported: { label: "미보고", tone: "neutral" },
    transport: { label: "전송 오류", tone: "warning" },
  };
  return labels[kind];
}
