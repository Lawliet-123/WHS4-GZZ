import { humanizeModule } from "./domain";
import { redactEventText, redactSensitiveText, sanitizeEvidence } from "./components/EvidenceDrawer";
import type { DashboardEvent, SnapshotResponse } from "./types";

export type PolicySignalStatus = "ACTIVE" | "INACTIVE" | "ADVISORY" | "DEFERRED" | "UNRESOLVED" | "UNAVAILABLE" | "UNKNOWN";
export type ModuleSignalSort = "latest" | "highest";

export interface PolicyEvidenceRow {
  key: string;
  module: string;
  submodule: string | null;
  label: string;
  status: PolicySignalStatus;
  latest: DashboardEvent | null;
  retained: DashboardEvent | null;
  threshold: number | null;
  thresholdMode: string | null;
  highestObserved: DashboardEvent | null;
  /** Only a Scoring aggregate signal can set this to ACTIVE. */
  authoritative: boolean;
}

const statuses = new Set<PolicySignalStatus>(["ACTIVE", "INACTIVE", "ADVISORY", "DEFERRED", "UNRESOLVED", "UNAVAILABLE", "UNKNOWN"]);
const record = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const text = (value: unknown): string | null => typeof value === "string" && value.trim() ? value : null;
const number = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const keyFor = (module: string, submodule: string | null) => JSON.stringify([module, submodule]);
const scopeFor = (event: Pick<DashboardEvent, "evidence">) => text(sanitizeEvidence(event.evidence).evidence.submodule);

function sameSubject(event: DashboardEvent, snapshot: SnapshotResponse): boolean {
  return event.session_id === snapshot.session_id && event.player_id === snapshot.player_id;
}

function fromState(value: unknown, snapshot: SnapshotResponse, module: string, submodule: string | null, events: readonly DashboardEvent[] = []): DashboardEvent | null {
  const item = record(value);
  if (!item || item.session_id !== snapshot.session_id || item.player_id !== snapshot.player_id || item.module !== module) return null;
  const evidence = record(item.evidence);
  const id = text(item.event_id) ?? text(item.id);
  const raw = number(item.raw_score), timestamp = number(item.timestamp_ms), sequence = number(item.sequence);
  if (!evidence || !id || raw === null || timestamp === null || sequence === null) return null;
  if (submodule !== null && text(evidence.submodule) !== submodule) return null;
  // Scoring's stored state has no receiver clock metadata. Only the matching
  // original Event can supply those clocks; another event/heartbeat cannot.
  const original = events.find((event) => event.id === id && sameSubject(event, snapshot)
    && event.module === module && scopeFor(event) === submodule && event.timestamp_ms === timestamp);
  const declaredBasis = typeof item.time_basis === "string" ? item.time_basis : "unknown";
  return {
    id, sequence, session_id: snapshot.session_id, player_id: snapshot.player_id,
    module, timestamp_ms: timestamp, raw_score: raw, evidence,
    reasons: Array.isArray(item.reasons) ? item.reasons.filter((reason): reason is string => typeof reason === "string") : [],
    event_kind: original?.event_kind ?? (item.event_kind === "operational" ? "operational" : "detection"),
    time_basis: original && original.time_basis !== "unknown" ? original.time_basis : declaredBasis,
    received_at_utc: original?.received_at_utc ?? text(item.received_at_utc),
    observed_at_utc: original?.observed_at_utc ?? text(item.observed_at_utc),
    evidence_image: null, log_excerpt: null,
  };
}

function labelFor(module: string, submodule: string | null, witness: DashboardEvent | null): string {
  const identifier = submodule && module === "external_access" ? submodule : module;
  const label = humanizeModule(identifier);
  const value = submodule && module !== "external_access" ? `${label} · ${submodule}` : label;
  return witness ? redactEventText(witness, value) : redactSensitiveText(value);
}

function latestEvent(events: readonly DashboardEvent[]): DashboardEvent | null {
  // This is Scoring latest_state order, not receiver arrival order.
  return events.reduce<DashboardEvent | null>((latest, event) => !latest
    || event.timestamp_ms > latest.timestamp_ms
    || event.timestamp_ms === latest.timestamp_ms && event.sequence > latest.sequence ? event : latest, null);
}

function highestEvent(events: readonly DashboardEvent[]): DashboardEvent | null {
  return events.filter((event) => number(event.raw_score) !== null)
    .reduce<DashboardEvent | null>((highest, event) => !highest
      || event.raw_score > highest.raw_score
      || event.raw_score === highest.raw_score && event.sequence > highest.sequence ? event : highest, null);
}

/** Read Scoring explanations without deriving ACTIVE from a positive raw value. */
export function policyEvidenceRows(snapshot: SnapshotResponse | null, events: readonly DashboardEvent[]): PolicyEvidenceRow[] {
  if (!snapshot) return [];
  const scopedEvents = events.filter((event) => sameSubject(event, snapshot));
  const rows = new Map<string, PolicyEvidenceRow>();
  const entries = Array.isArray(snapshot.policy.module_evidence) ? snapshot.policy.module_evidence : [];
  for (const value of entries) {
    const entry = record(value);
    const module = text(entry?.module);
    if (!entry || !module) continue;
    const submodule = text(entry.submodule);
    const signal = record(entry.signal);
    const status = text(signal?.status);
    const latest = fromState(entry.latest_event, snapshot, module, submodule, scopedEvents);
    const parsedRetained = fromState(entry.retained_incident_event, snapshot, module, submodule, scopedEvents);
    const observations = scopedEvents.filter((event) => event.module === module && scopeFor(event) === submodule);
    // A stale/inconsistent retained field cannot turn an INACTIVE signal ACTIVE.
    const retained = status === "ACTIVE" ? parsedRetained : null;
    const key = keyFor(module, submodule);
    rows.set(key, {
      key, module, submodule, label: labelFor(module, submodule, retained ?? latest),
      status: statuses.has(status as PolicySignalStatus) ? status as PolicySignalStatus : "UNKNOWN",
      latest, retained, threshold: number(signal?.calibration_threshold),
      thresholdMode: text(signal?.calibration_mode), highestObserved: highestEvent(observations),
      authoritative: statuses.has(status as PolicySignalStatus) && status !== "UNKNOWN",
    });
  }

  const aggregate = record(snapshot.policy.aggregate_evidence);
  const signals = Array.isArray(aggregate?.signals) ? aggregate.signals : [];
  for (const state of snapshot.modules) {
    // A module-level external_access aggregate must not duplicate its scoped channels.
    if (state.module === "external_access" && [...rows.values()].some((row) => row.module === state.module)) continue;
    const submodule = scopeFor(state);
    const key = keyFor(state.module, submodule);
    if (rows.has(key)) continue;
    const latest = fromState(state, snapshot, state.module, submodule, scopedEvents);
    const rawSignal = signals.map(record).find((signal) => signal?.module === state.module);
    const status = text(rawSignal?.status);
    rows.set(key, {
      key, module: state.module, submodule, label: labelFor(state.module, submodule, latest),
      status: statuses.has(status as PolicySignalStatus) ? status as PolicySignalStatus : "UNKNOWN",
      latest, retained: null, threshold: number(rawSignal?.calibration_threshold), thresholdMode: text(rawSignal?.calibration_mode),
      highestObserved: highestEvent(scopedEvents.filter((event) => event.module === state.module && scopeFor(event) === submodule)),
      authoritative: statuses.has(status as PolicySignalStatus) && status !== "UNKNOWN",
    });
  }

  for (const event of scopedEvents) {
    const submodule = scopeFor(event), key = keyFor(event.module, submodule);
    if (rows.has(key)) continue;
    const observations = scopedEvents.filter((candidate) => candidate.module === event.module && scopeFor(candidate) === submodule);
    const latest = latestEvent(observations);
    rows.set(key, {
      key, module: event.module, submodule, label: labelFor(event.module, submodule, latest), status: "UNKNOWN",
      latest, retained: null, threshold: null, thresholdMode: null, highestObserved: highestEvent(observations), authoritative: false,
    });
  }
  return [...rows.values()];
}

export function sortModuleSignals(rows: readonly PolicyEvidenceRow[], order: ModuleSignalSort): PolicyEvidenceRow[] {
  return [...rows].sort((left, right) => {
    const leftValue = order === "latest" ? left.latest?.raw_score : left.highestObserved?.raw_score;
    const rightValue = order === "latest" ? right.latest?.raw_score : right.highestObserved?.raw_score;
    if (leftValue === undefined && rightValue !== undefined) return 1;
    if (leftValue !== undefined && rightValue === undefined) return -1;
    if (leftValue !== undefined && rightValue !== undefined && leftValue !== rightValue) return rightValue - leftValue;
    return left.label.localeCompare(right.label, "ko");
  });
}

export function representativeReason(row: PolicyEvidenceRow): string {
  const event = row.retained ?? row.latest;
  return event?.reasons.length ? event.reasons.map((reason) => redactEventText(event, reason)).join(" · ") : "근거 미제공";
}
