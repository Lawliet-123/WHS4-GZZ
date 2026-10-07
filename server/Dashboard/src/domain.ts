import type {
  Assessment,
  ComponentStatus,
  DashboardEvent,
  DashboardFilters,
  OverviewResponse,
  TimelineBucket,
  VerdictStatus,
} from "./types";
import { eventBelongsToModule, labelForModuleIdentifier } from "./moduleCatalog";

export const defaultFilters: DashboardFilters = {
  sessionId: "ALL",
  playerId: "ALL",
  module: "ALL",
  submodule: "ALL",
  eventKind: "ALL",
  verdict: "ALL",
  query: "",
};

export const verdictMeta: Record<
  VerdictStatus,
  { label: string; short: string; tone: string }
> = {
  SUSPICIOUS: {
    label: "의심 근거 있음",
    short: "검토 필요",
    tone: "danger",
  },
  INCONCLUSIVE: {
    label: "판단 보류",
    short: "평가 불완전",
    tone: "warning",
  },
  NO_ACTIVE_EVIDENCE: {
    label: "활성 근거 없음",
    short: "활성 근거 없음",
    tone: "success",
  },
  UNKNOWN: {
    label: "판정 없음",
    short: "확인 불가",
    tone: "neutral",
  },
};

export const componentMeta: Record<ComponentStatus, { label: string; tone: string }> = {
  starting: { label: "시작 중", tone: "info" },
  running: { label: "작동 중", tone: "success" },
  healthy: { label: "정상 수신", tone: "success" },
  degraded: { label: "일부 저하", tone: "warning" },
  failed: { label: "실패", tone: "danger" },
  stopped: { label: "중지", tone: "neutral" },
  stale: { label: "응답 지연", tone: "warning" },
  unknown: { label: "확인 불가", tone: "neutral" },
};

export function connectionMeta(state: string): { label: string; tone: string } {
  const labels: Record<string, { label: string; tone: string }> = {
    online: { label: "온라인", tone: "success" },
    healthy: { label: "정상 수신", tone: "success" },
    running: { label: "작동 중", tone: "success" },
    starting: { label: "시작 중", tone: "info" },
    stopping: { label: "종료 중", tone: "info" },
    degraded: { label: "일부 저하", tone: "warning" },
    stale: { label: "응답 지연", tone: "warning" },
    failed: { label: "실패", tone: "danger" },
    unavailable: { label: "사용 불가", tone: "danger" },
    stopped: { label: "중지", tone: "neutral" },
    unknown: { label: "확인 불가", tone: "neutral" },
  };
  return Object.hasOwn(labels, state) ? labels[state]! : { label: state || "확인 불가", tone: "neutral" };
}

export const subjectKey = (sessionId: string, playerId: string) => `${sessionId}::${playerId}`;

function normalized(value: string): string {
  return value.trim().toLocaleLowerCase("ko-KR");
}

function searchableEvidence(evidence: Record<string, unknown>): string {
  try {
    return JSON.stringify(evidence);
  } catch {
    return "";
  }
}

function eventMatchesQuery(event: DashboardEvent, query: string): boolean {
  const needle = normalized(query);
  if (!needle) return true;
  const haystack = [
    event.id,
    event.session_id,
    event.player_id,
    event.module,
    ...event.reasons,
    searchableEvidence(event.evidence),
  ].join(" ").toLocaleLowerCase("ko-KR");
  return haystack.includes(needle);
}

function assessmentList(source: Assessment[] | OverviewResponse): Assessment[] {
  return Array.isArray(source) ? source : source.assessments;
}

export function filterEvents(
  events: DashboardEvent[],
  assessmentSource: Assessment[] | OverviewResponse,
  filters: DashboardFilters,
): DashboardEvent[] {
  const verdictBySubject = new Map(
    assessmentList(assessmentSource).map((assessment) => [
      subjectKey(assessment.session_id, assessment.player_id),
      assessment.status,
    ]),
  );

  return events
    .filter((item) => filters.sessionId === "ALL" || item.session_id === filters.sessionId)
    .filter((item) => filters.playerId === "ALL" || item.player_id === filters.playerId)
    .filter((item) => eventBelongsToModule(item, filters.module))
    .filter((item) => {
      if (filters.submodule === "ALL") return true;
      return item.evidence.submodule === filters.submodule;
    })
    .filter((item) => filters.eventKind === "ALL" || item.event_kind === filters.eventKind)
    .filter((item) => {
      if (filters.verdict === "ALL") return true;
      return verdictBySubject.get(subjectKey(item.session_id, item.player_id)) === filters.verdict;
    })
    .filter((item) => eventMatchesQuery(item, filters.query))
    .slice()
    .sort((a, b) => b.sequence - a.sequence);
}

/** Alias that makes the data type explicit at new call sites. */
export const filterDashboardEvents = filterEvents;

export function filterAssessments(
  assessments: Assessment[],
  events: DashboardEvent[],
  filters: DashboardFilters,
): Assessment[] {
  const scopedEvents = events
    .filter((item) => eventBelongsToModule(item, filters.module))
    .filter((item) => filters.submodule === "ALL" || item.evidence.submodule === filters.submodule)
    .filter((item) => filters.eventKind === "ALL" || item.event_kind === filters.eventKind);
  const relevantSubjects = new Set(
    scopedEvents.map((item) => subjectKey(item.session_id, item.player_id)),
  );
  const query = normalized(filters.query);
  const queryMatchedSubjects = new Set(
    query
      ? scopedEvents
        .filter((item) => eventMatchesQuery(item, query))
        .map((item) => subjectKey(item.session_id, item.player_id))
      : [],
  );

  return assessments.filter((assessment) => {
    if (filters.sessionId !== "ALL" && assessment.session_id !== filters.sessionId) return false;
    if (filters.playerId !== "ALL" && assessment.player_id !== filters.playerId) return false;
    if (filters.verdict !== "ALL" && assessment.status !== filters.verdict) return false;
    if ((filters.module !== "ALL" || filters.submodule !== "ALL" || filters.eventKind !== "ALL")
      && !relevantSubjects.has(subjectKey(assessment.session_id, assessment.player_id))) return false;
    if (!query) return true;
    const haystack = [
      assessment.id,
      assessment.session_id,
      assessment.player_id,
      assessment.status,
      ...assessment.reason_codes,
      ...(assessment.final_verdict?.active_modules ?? []),
      ...(assessment.final_verdict?.unresolved_modules ?? []),
    ].join(" ").toLocaleLowerCase("ko-KR");
    return haystack.includes(query) || queryMatchedSubjects.has(subjectKey(assessment.session_id, assessment.player_id));
  });
}

export function buildTimelineBuckets(
  events: DashboardEvent[],
  bucketCount = 12,
): TimelineBucket[] {
  if (!Number.isInteger(bucketCount) || bucketCount < 1) return [];
  const maximum = Math.max(1, ...events.map((item) => Math.max(0, item.timestamp_ms)));
  const width = Math.max(1, Math.ceil((maximum + 1) / bucketCount));
  const buckets: TimelineBucket[] = Array.from({ length: bucketCount }, (_, index) => ({
    startMs: index * width,
    endMs: (index + 1) * width,
    total: 0,
    detections: 0,
    operational: 0,
  }));

  for (const item of events) {
    const index = Math.min(bucketCount - 1, Math.floor(Math.max(0, item.timestamp_ms) / width));
    const bucket = buckets[index];
    if (!bucket) continue;
    bucket.total += 1;
    if (item.event_kind === "detection") bucket.detections += 1;
    if (item.event_kind === "operational") bucket.operational += 1;
  }
  return buckets;
}

export function formatElapsed(timestampMs: number): string {
  if (!Number.isFinite(timestampMs)) return "--:--";
  const totalSeconds = Math.max(0, Math.floor(timestampMs / 1_000));
  const hours = Math.floor(totalSeconds / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
  }
  return [minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "확인 불가";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "확인 불가";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatAge(ageMs: number): string {
  if (!Number.isFinite(ageMs) || ageMs < 0) return "확인 불가";
  if (ageMs < 1_000) return "방금 전";
  if (ageMs < 60_000) return `${Math.floor(ageMs / 1_000)}초 전`;
  if (ageMs < 3_600_000) return `${Math.floor(ageMs / 60_000)}분 전`;
  return `${Math.floor(ageMs / 3_600_000)}시간 전`;
}

export function shortenId(value: string, maximum = 34): string {
  if (value.length <= maximum || maximum < 9) return value;
  const available = maximum - 1;
  const start = Math.ceil(available * 0.62);
  const end = available - start;
  return `${value.slice(0, start)}…${value.slice(-end)}`;
}

export function humanizeModule(module: string): string {
  const labels: Record<string, string> = {
    esp: "ESP 접근 감시",
    external_access: "외부 접근",
    external_process: "외부 프로세스",
    module_integrity: "DLL 무결성",
    module_health: "프로세스 생존",
    file_integrity: "자체 파일 무결성",
    overlay_correlation: "오버레이 상관관계",
    noclip: "이동·충돌",
    aimbot: "입력 행동",
    autopaint: "자동 페인트",
    godmode: "상태 변조",
    hide_anywhere: "숨기 상태",
    selfdefense: "Self Defense",
    launcher: "Launcher",
    receiver: "Receiver",
    scoring: "Scoring",
  };
  return (Object.hasOwn(labels, module) ? labels[module] : null) ?? labelForModuleIdentifier(module) ?? `미등록 · ${module.replaceAll("_", " ")}`;
}

export function humanizeReason(reason: string): string {
  const labels: Record<string, string> = {
    CALIBRATED_ACTIVE_EVIDENCE: "보정된 활성 근거가 확인됨",
    ASSESSMENT_INCOMPLETE: "일부 관측 또는 평가가 완료되지 않음",
    NO_ACTIVE_EVIDENCE: "현재 평가 범위에 활성 근거가 없음",
    ADVISORY_EVIDENCE_PRESENT: "참고용 근거가 함께 존재함",
    MISSING_MEASUREMENT: "필수 관측 미수신",
    STALE_MEASUREMENT: "필수 관측 유효기간 초과",
  };
  return Object.hasOwn(labels, reason) ? labels[reason]! : reason.replaceAll("_", " ");
}

export function eventTitle(item: DashboardEvent): string {
  if (item.reasons.length > 0) return humanizeReason(item.reasons[0]!);
  if (item.event_kind === "operational") return `${humanizeModule(item.module)} 상태 이벤트`;
  return `${humanizeModule(item.module)} 탐지 이벤트`;
}

export function eventSummary(item: DashboardEvent): string {
  if (item.reasons.length > 1) return item.reasons.slice(1).map(humanizeReason).join(" · ");
  return `원본 점수 ${item.raw_score} · 서버 수신 순서 ${item.sequence}`;
}
