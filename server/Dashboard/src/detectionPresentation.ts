import { redactSensitiveText, sanitizeEvidence } from "./components/EvidenceDrawer";
import { humanizeModule } from "./domain";
import type { Assessment, DashboardEvent, LauncherOverviewStatus, VerdictStatus } from "./types";

export type DetectionSeverity = "Critical" | "High" | "Medium" | "Low";
export type DetectionSortKey = "sequence" | "score" | "severity";
export type SortDirection = "asc" | "desc";

const severityValues: Record<string, DetectionSeverity> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
};
const severityRank: Record<DetectionSeverity, number> = { Critical: 4, High: 3, Medium: 2, Low: 1 };

/** Optional server evidence is sanitized in the same way as the detail drawer. */
export function optionalEvidenceText(event: DashboardEvent, key: string): string | null {
  const safe = sanitizeEvidence(event.evidence).evidence;
  const value = Object.hasOwn(safe, key) ? safe[key] : null;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** Severity is only an explicit server field, never inferred from scores or verdicts. */
export function explicitSeverity(event: DashboardEvent): DetectionSeverity | null {
  const value = optionalEvidenceText(event, "severity")?.toLowerCase();
  return value && Object.hasOwn(severityValues, value) ? severityValues[value]! : null;
}

function safeModuleLabel(identifier: string): string {
  const label = humanizeModule(identifier);
  return redactSensitiveText(typeof label === "string" ? label : `미등록 · ${identifier.replaceAll("_", " ")}`);
}

export function detectionType(event: DashboardEvent): string {
  const explicitType = optionalEvidenceText(event, "event_type");
  if (explicitType) return explicitType;
  if (event.event_kind === "operational") return "운영 상태";
  const submodule = optionalEvidenceText(event, "submodule");
  return submodule
    ? safeModuleLabel(submodule)
    : `${safeModuleLabel(event.module)} 관측`;
}

/** Count distinct reporting clients, not session/player rows or stopped sources. */
export function connectedClientCount(statuses: readonly LauncherOverviewStatus[]): number | null {
  let hasClientIdentity = false;
  const clients = new Set<string>();
  for (const status of statuses) {
    const clientId = status.source?.client_id;
    if (typeof clientId !== "string" || !clientId.trim()) continue;
    hasClientIdentity = true;
    if (status.connected && (status.state === "healthy" || status.state === "online")) clients.add(clientId.trim());
  }
  return hasClientIdentity ? clients.size : null;
}

/** Display the current authoritative verdict for the same session/player pair. */
export function eventVerdict(event: DashboardEvent, assessments: readonly Assessment[]): VerdictStatus {
  return assessments.find((assessment) => assessment.session_id === event.session_id
    && assessment.player_id === event.player_id)?.status ?? "UNKNOWN";
}

export function sortDetectionEvents(
  events: readonly DashboardEvent[],
  key: DetectionSortKey,
  direction: SortDirection,
): DashboardEvent[] {
  const factor = direction === "asc" ? 1 : -1;
  const entries = events.map((event, index) => {
    const severity = key === "severity" ? explicitSeverity(event) : null;
    const value = key === "sequence" ? event.sequence
      : key === "score" ? (Number.isFinite(event.raw_score) ? event.raw_score : null)
        : severity ? severityRank[severity] : null;
    return { event, index, value };
  });
  entries.sort((a, b) => {
    // Unprovided severity (and malformed numerical input) stays last in either direction.
    if (a.value === null && b.value !== null) return 1;
    if (a.value !== null && b.value === null) return -1;
    if (a.value !== null && b.value !== null && a.value !== b.value) return (a.value - b.value) * factor;
    // Sequence is receiver ingest order; relative gameplay timestamps cannot be compared across sessions.
    return b.event.sequence - a.event.sequence || a.index - b.index;
  });
  return entries.map(({ event }) => event);
}
