import { resolveEventModule } from "./moduleCatalog";
import type { Assessment, DashboardEvent, VerdictStatus } from "./types";

export interface VerdictDistributionItem {
  status: VerdictStatus;
  count: number;
}

export interface DetectorObservationCount {
  id: string;
  label: string;
  count: number;
  positiveCount: number;
}

export interface SessionObservationPoint {
  sequenceStart: number;
  sequenceEnd: number;
  count: number;
  positiveCount: number;
}

const verdictOrder: readonly VerdictStatus[] = ["SUSPICIOUS", "INCONCLUSIVE", "NO_ACTIVE_EVIDENCE", "UNKNOWN"];

function latestEvents(events: readonly DashboardEvent[]): DashboardEvent[] {
  const records = new Map<string, DashboardEvent>();
  for (const event of events) records.set(event.id, event);
  return [...records.values()];
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

/** Count the current server verdict once per session/player pair. */
export function verdictDistribution(assessments: readonly Assessment[]): VerdictDistributionItem[] {
  const latest = new Map<string, Assessment>();
  for (const assessment of assessments) latest.set(JSON.stringify([assessment.session_id, assessment.player_id]), assessment);
  const counts = new Map<VerdictStatus, number>(verdictOrder.map((status) => [status, 0]));
  for (const assessment of latest.values()) {
    const status = counts.has(assessment.status) ? assessment.status : "UNKNOWN";
    counts.set(status, counts.get(status)! + 1);
  }
  return verdictOrder.map((status) => ({ status, count: counts.get(status)! }));
}

/** Positive observations are raw_score > 0, not cheating verdicts or summed risk. */
export function detectorObservationCounts(events: readonly DashboardEvent[]): DetectorObservationCount[] {
  const detectors = new Map<string, DetectorObservationCount>();
  for (const event of latestEvents(events)) {
    if (event.event_kind !== "detection") continue;
    const detector = resolveEventModule(event);
    let count = detectors.get(detector.id);
    if (!count) {
      count = { id: detector.id, label: detector.label, count: 0, positiveCount: 0 };
      detectors.set(detector.id, count);
    }
    count.count += 1;
    if (event.raw_score > 0) count.positiveCount += 1;
  }
  return [...detectors.values()].sort((a, b) => b.count - a.count || compareText(a.label, b.label) || compareText(a.id, b.id));
}

/**
 * Fixed receiver-sequence ranges, never gameplay-time buckets. Empty intervals
 * mean no selected-session records in that ingest range, not elapsed inactivity.
 */
export function sessionObservationFlow(
  events: readonly DashboardEvent[],
  sessionId: string,
  maxPoints = 12,
): SessionObservationPoint[] {
  if (!Number.isFinite(maxPoints) || maxPoints < 1) return [];
  const ordered = latestEvents(events)
    .filter((event) => event.event_kind === "detection" && event.session_id === sessionId
      && Number.isFinite(event.sequence) && Number.isInteger(event.sequence) && event.sequence >= 0)
    .sort((a, b) => a.sequence - b.sequence || compareText(a.id, b.id));
  if (!ordered.length) return [];
  const firstSequence = ordered[0]!.sequence;
  const lastSequence = ordered.at(-1)!.sequence;
  const range = lastSequence - firstSequence + 1;
  const interval = Math.max(1, Math.ceil(range / Math.floor(maxPoints)));
  const binCount = Math.min(Math.floor(maxPoints), Math.ceil(range / interval));
  const points: SessionObservationPoint[] = [];
  let eventIndex = 0;
  for (let index = 0; index < binCount; index += 1) {
    const sequenceStart = firstSequence + index * interval;
    const sequenceEnd = Math.min(lastSequence, sequenceStart + interval - 1);
    let count = 0;
    let positiveCount = 0;
    while (eventIndex < ordered.length && ordered[eventIndex]!.sequence <= sequenceEnd) {
      count += 1;
      if (ordered[eventIndex]!.raw_score > 0) positiveCount += 1;
      eventIndex += 1;
    }
    points.push({ sequenceStart, sequenceEnd, count, positiveCount });
  }
  return points;
}
