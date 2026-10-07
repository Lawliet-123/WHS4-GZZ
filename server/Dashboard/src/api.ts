import type {
  DashboardBundle,
  DashboardEvent,
  DashboardFilters,
  EventListResponse,
  EventQuery,
  GodModeHistoryResponse,
  LiveConnectionInput,
  OverviewResponse,
  PaginationLoadState,
  SnapshotResponse,
  SubjectDetail,
  SubjectStatusResponse,
} from "./types";

const REQUEST_TIMEOUT_MS = 8_000;
const MAX_OVERVIEW_PAGES = 250;
const MAX_EVENT_PAGES = 250;

type JsonObject = Record<string, unknown>;

const VERDICT_STATUSES = new Set(["SUSPICIOUS", "INCONCLUSIVE", "NO_ACTIVE_EVIDENCE", "UNKNOWN"]);
const DATA_STATES = new Set(["available", "missing", "not_connected"]);
const COMPONENT_STATES = new Set([
  "starting",
  "running",
  "healthy",
  "degraded",
  "failed",
  "stopped",
  "stale",
  "unknown",
]);
const CONNECTION_STATES = new Set([...COMPONENT_STATES, "online", "unavailable", "stopping"]);

export class DashboardApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "DashboardApiError";
    this.status = status;
  }
}

function invalidContract(endpoint: string, path: string, status: number): never {
  throw new DashboardApiError(
    `Dashboard 서버의 ${endpoint} 응답 형식이 올바르지 않습니다. (${path})`,
    status,
  );
}

function objectAt(value: unknown, endpoint: string, path: string, status: number): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return invalidContract(endpoint, path, status);
  }
  return value as JsonObject;
}

function arrayAt(value: unknown, endpoint: string, path: string, status: number): unknown[] {
  if (!Array.isArray(value)) return invalidContract(endpoint, path, status);
  return value;
}

function stringAt(
  value: unknown,
  endpoint: string,
  path: string,
  status: number,
  allowEmpty = true,
): string {
  if (typeof value !== "string" || (!allowEmpty && value.length === 0)) {
    return invalidContract(endpoint, path, status);
  }
  return value;
}

function booleanAt(value: unknown, endpoint: string, path: string, status: number): boolean {
  if (typeof value !== "boolean") return invalidContract(endpoint, path, status);
  return value;
}

function finiteNumberAt(value: unknown, endpoint: string, path: string, status: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return invalidContract(endpoint, path, status);
  }
  return value;
}

function nonNegativeIntegerAt(value: unknown, endpoint: string, path: string, status: number): number {
  const number = finiteNumberAt(value, endpoint, path, status);
  if (!Number.isInteger(number) || number < 0) return invalidContract(endpoint, path, status);
  return number;
}

function nullableNumberAt(value: unknown, endpoint: string, path: string, status: number): number | null {
  return value === null ? null : finiteNumberAt(value, endpoint, path, status);
}

function nullableStringAt(value: unknown, endpoint: string, path: string, status: number): string | null {
  return value === null ? null : stringAt(value, endpoint, path, status);
}

function enumAt(value: unknown, allowed: Set<string>, endpoint: string, path: string, status: number): string {
  const candidate = stringAt(value, endpoint, path, status);
  if (!allowed.has(candidate)) return invalidContract(endpoint, path, status);
  return candidate;
}

function stringArrayAt(value: unknown, endpoint: string, path: string, status: number): string[] {
  const items = arrayAt(value, endpoint, path, status);
  items.forEach((item, index) => stringAt(item, endpoint, `${path}[${index}]`, status));
  return items as string[];
}

function validateIndex(value: unknown, endpoint: string, path: string, status: number): void {
  const index = objectAt(value, endpoint, path, status);
  nonNegativeIntegerAt(index.through_sequence, endpoint, `${path}.through_sequence`, status);
  booleanAt(index.catching_up, endpoint, `${path}.catching_up`, status);
}

function validateEvent(value: unknown, endpoint: string, path: string, status: number): void {
  const event = objectAt(value, endpoint, path, status);
  stringAt(event.id, endpoint, `${path}.id`, status, false);
  nonNegativeIntegerAt(event.sequence, endpoint, `${path}.sequence`, status);
  stringAt(event.session_id, endpoint, `${path}.session_id`, status, false);
  stringAt(event.player_id, endpoint, `${path}.player_id`, status, false);
  stringAt(event.module, endpoint, `${path}.module`, status, false);
  finiteNumberAt(event.timestamp_ms, endpoint, `${path}.timestamp_ms`, status);
  objectAt(event.evidence, endpoint, `${path}.evidence`, status);
  stringArrayAt(event.reasons, endpoint, `${path}.reasons`, status);
  finiteNumberAt(event.raw_score, endpoint, `${path}.raw_score`, status);
  enumAt(event.event_kind, new Set(["detection", "operational"]), endpoint, `${path}.event_kind`, status);
  stringAt(event.time_basis, endpoint, `${path}.time_basis`, status);
  for (const key of ["received_at_utc", "observed_at_utc"]) {
    if (event[key] !== undefined) nullableStringAt(event[key], endpoint, `${path}.${key}`, status);
  }
  nullableStringAt(event.evidence_image, endpoint, `${path}.evidence_image`, status);
  nullableStringAt(event.log_excerpt, endpoint, `${path}.log_excerpt`, status);
}

function validateFinalVerdict(value: unknown, endpoint: string, path: string, status: number): void {
  const verdict = objectAt(value, endpoint, path, status);
  stringAt(verdict.version, endpoint, `${path}.version`, status, false);
  stringAt(verdict.session_id, endpoint, `${path}.session_id`, status, false);
  stringAt(verdict.player_id, endpoint, `${path}.player_id`, status, false);
  enumAt(verdict.status, VERDICT_STATUSES, endpoint, `${path}.status`, status);
  booleanAt(verdict.assessment_complete, endpoint, `${path}.assessment_complete`, status);
  nonNegativeIntegerAt(verdict.evidence_unit_count, endpoint, `${path}.evidence_unit_count`, status);
  nonNegativeIntegerAt(verdict.active_module_count, endpoint, `${path}.active_module_count`, status);
  nonNegativeIntegerAt(verdict.overlap_adjustment_count, endpoint, `${path}.overlap_adjustment_count`, status);
  for (const key of [
    "active_modules",
    "advisory_modules",
    "unresolved_modules",
    "deferred_modules",
    "unavailable_modules",
    "reason_codes",
  ]) {
    stringArrayAt(verdict[key], endpoint, `${path}.${key}`, status);
  }
}

function validateAssessment(value: unknown, endpoint: string, path: string, status: number): void {
  const assessment = objectAt(value, endpoint, path, status);
  stringAt(assessment.id, endpoint, `${path}.id`, status, false);
  stringAt(assessment.session_id, endpoint, `${path}.session_id`, status, false);
  stringAt(assessment.player_id, endpoint, `${path}.player_id`, status, false);
  enumAt(assessment.status, VERDICT_STATUSES, endpoint, `${path}.status`, status);
  booleanAt(assessment.assessment_available, endpoint, `${path}.assessment_available`, status);
  nullableNumberAt(assessment.score, endpoint, `${path}.score`, status);
  nullableNumberAt(assessment.confidence, endpoint, `${path}.confidence`, status);
  if (assessment.final_verdict !== null) {
    validateFinalVerdict(assessment.final_verdict, endpoint, `${path}.final_verdict`, status);
  }
  stringArrayAt(assessment.reason_codes, endpoint, `${path}.reason_codes`, status);
  enumAt(assessment.data_state, DATA_STATES, endpoint, `${path}.data_state`, status);
  arrayAt(assessment.module_scores, endpoint, `${path}.module_scores`, status);
  stringArrayAt(assessment.reasons, endpoint, `${path}.reasons`, status);
}

function validateComponent(value: unknown, endpoint: string, path: string, status: number): void {
  const component = objectAt(value, endpoint, path, status);
  stringAt(component.id, endpoint, `${path}.id`, status, false);
  enumAt(component.state, COMPONENT_STATES, endpoint, `${path}.state`, status);
  enumAt(component.reported_status, COMPONENT_STATES, endpoint, `${path}.reported_status`, status);
  booleanAt(component.required, endpoint, `${path}.required`, status);
  if (component.pid !== null) nonNegativeIntegerAt(component.pid, endpoint, `${path}.pid`, status);
  nonNegativeIntegerAt(component.updated_at_ms, endpoint, `${path}.updated_at_ms`, status);
  nonNegativeIntegerAt(component.stale_after_ms, endpoint, `${path}.stale_after_ms`, status);
  nonNegativeIntegerAt(component.age_ms, endpoint, `${path}.age_ms`, status);
  nonNegativeIntegerAt(component.effective_age_ms, endpoint, `${path}.effective_age_ms`, status);
  objectAt(component.details, endpoint, `${path}.details`, status);
}

function validateStatusSource(value: unknown, endpoint: string, path: string, status: number): void {
  const source = objectAt(value, endpoint, path, status);
  stringAt(source.client_id, endpoint, `${path}.client_id`, status, false);
  enumAt(source.role, new Set(["launcher", "component"]), endpoint, `${path}.role`, status);
  nonNegativeIntegerAt(source.sequence, endpoint, `${path}.sequence`, status);
  stringAt(source.received_at_utc, endpoint, `${path}.received_at_utc`, status, false);
  nonNegativeIntegerAt(source.age_ms, endpoint, `${path}.age_ms`, status);
  enumAt(source.state, COMPONENT_STATES, endpoint, `${path}.state`, status);
  enumAt(source.reported_status, COMPONENT_STATES, endpoint, `${path}.reported_status`, status);
  arrayAt(source.components, endpoint, `${path}.components`, status).forEach((component, index) => {
    validateComponent(component, endpoint, `${path}.components[${index}]`, status);
  });
  const transport = objectAt(source.transport, endpoint, `${path}.transport`, status);
  booleanAt(transport.configured, endpoint, `${path}.transport.configured`, status);
  nonNegativeIntegerAt(transport.consecutive_failures, endpoint, `${path}.transport.consecutive_failures`, status);
  if (transport.last_success_sequence !== null) {
    nonNegativeIntegerAt(transport.last_success_sequence, endpoint, `${path}.transport.last_success_sequence`, status);
  }
  nullableStringAt(transport.last_error_type, endpoint, `${path}.transport.last_error_type`, status);
}

function validateLauncher(value: unknown, endpoint: string, path: string, status: number): void {
  const launcher = objectAt(value, endpoint, path, status);
  enumAt(launcher.state, CONNECTION_STATES, endpoint, `${path}.state`, status);
  booleanAt(launcher.connected, endpoint, `${path}.connected`, status);
  if (launcher.source !== null) validateStatusSource(launcher.source, endpoint, `${path}.source`, status);
  stringAt(launcher.reason, endpoint, `${path}.reason`, status);
}

function decodeOverview(value: unknown, status: number): OverviewResponse {
  const endpoint = "overview";
  const overview = objectAt(value, endpoint, "$", status);
  stringAt(overview.schema_version, endpoint, "schema_version", status, false);
  stringAt(overview.generated_at_utc, endpoint, "generated_at_utc", status, false);

  const capabilities = objectAt(overview.capabilities, endpoint, "capabilities", status);
  for (const key of ["final_assessment", "launcher_heartbeat", "evidence_images", "heartbeat_query"]) {
    booleanAt(capabilities[key], endpoint, `capabilities.${key}`, status);
  }

  const connection = objectAt(overview.connection, endpoint, "connection", status);
  for (const key of ["Receiver", "Scoring", "Launcher"]) {
    const probe = objectAt(connection[key], endpoint, `connection.${key}`, status);
    enumAt(probe.state, CONNECTION_STATES, endpoint, `connection.${key}.state`, status);
    stringAt(probe.scope, endpoint, `connection.${key}.scope`, status);
  }

  const counts = objectAt(overview.counts, endpoint, "counts", status);
  stringAt(counts.scope, endpoint, "counts.scope", status, false);
  for (const key of ["events", "sessions", "players", "operational_events"]) {
    nonNegativeIntegerAt(counts[key], endpoint, `counts.${key}`, status);
  }
  nullableNumberAt(counts.review, endpoint, "counts.review", status);
  nullableNumberAt(counts.high, endpoint, "counts.high", status);

  arrayAt(overview.sessions, endpoint, "sessions", status).forEach((value, index) => {
    const session = objectAt(value, endpoint, `sessions[${index}]`, status);
    stringAt(session.id, endpoint, `sessions[${index}].id`, status, false);
    stringArrayAt(session.player_ids, endpoint, `sessions[${index}].player_ids`, status);
    stringArrayAt(session.module_ids, endpoint, `sessions[${index}].module_ids`, status);
    nullableNumberAt(session.duration_ms, endpoint, `sessions[${index}].duration_ms`, status);
    nullableNumberAt(session.max_observed_timestamp_ms, endpoint, `sessions[${index}].max_observed_timestamp_ms`, status);
    enumAt(session.status, VERDICT_STATUSES, endpoint, `sessions[${index}].status`, status);
    nullableNumberAt(session.score, endpoint, `sessions[${index}].score`, status);
  });
  arrayAt(overview.players, endpoint, "players", status).forEach((value, index) => {
    const player = objectAt(value, endpoint, `players[${index}]`, status);
    stringAt(player.id, endpoint, `players[${index}].id`, status, false);
    stringAt(player.display_name, endpoint, `players[${index}].display_name`, status);
    stringAt(player.identity_type, endpoint, `players[${index}].identity_type`, status);
    stringArrayAt(player.session_ids, endpoint, `players[${index}].session_ids`, status);
    enumAt(player.status, VERDICT_STATUSES, endpoint, `players[${index}].status`, status);
    nullableNumberAt(player.max_score, endpoint, `players[${index}].max_score`, status);
  });
  arrayAt(overview.assessments, endpoint, "assessments", status).forEach((assessment, index) => {
    validateAssessment(assessment, endpoint, `assessments[${index}]`, status);
  });

  const sessionPage = objectAt(overview.session_page, endpoint, "session_page", status);
  nullableStringAt(sessionPage.next_after_session, endpoint, "session_page.next_after_session", status);
  booleanAt(sessionPage.has_more, endpoint, "session_page.has_more", status);
  arrayAt(overview.events, endpoint, "events", status).forEach((event, index) => {
    validateEvent(event, endpoint, `events[${index}]`, status);
  });
  arrayAt(overview.module_statuses, endpoint, "module_statuses", status).forEach((value, index) => {
    const moduleStatus = objectAt(value, endpoint, `module_statuses[${index}]`, status);
    validateComponent(moduleStatus, endpoint, `module_statuses[${index}]`, status);
    for (const key of ["label", "status_id", "session_id", "player_id", "client_id", "last_seen_at"]) {
      stringAt(moduleStatus[key], endpoint, `module_statuses[${index}].${key}`, status, false);
    }
  });
  arrayAt(overview.launcher_statuses, endpoint, "launcher_statuses", status).forEach((value, index) => {
    const launcher = objectAt(value, endpoint, `launcher_statuses[${index}]`, status);
    stringAt(launcher.session_id, endpoint, `launcher_statuses[${index}].session_id`, status, false);
    stringAt(launcher.player_id, endpoint, `launcher_statuses[${index}].player_id`, status, false);
    validateLauncher(launcher, endpoint, `launcher_statuses[${index}]`, status);
  });
  stringAt(overview.events_endpoint, endpoint, "events_endpoint", status, false);
  validateIndex(overview.index, endpoint, "index", status);
  return overview as unknown as OverviewResponse;
}

function decodeEvents(value: unknown, status: number): EventListResponse {
  const endpoint = "events";
  const response = objectAt(value, endpoint, "$", status);
  arrayAt(response.items, endpoint, "items", status).forEach((event, index) => {
    validateEvent(event, endpoint, `items[${index}]`, status);
  });
  nullableStringAt(response.next_cursor, endpoint, "next_cursor", status);
  booleanAt(response.has_more, endpoint, "has_more", status);
  nonNegativeIntegerAt(response.through_sequence, endpoint, "through_sequence", status);
  validateIndex(response.index, endpoint, "index", status);
  return response as unknown as EventListResponse;
}

function decodeEvent(value: unknown, status: number): DashboardEvent {
  validateEvent(value, "event detail", "$", status);
  return value as DashboardEvent;
}

function decodeSnapshot(value: unknown, status: number): SnapshotResponse {
  const endpoint = "snapshot";
  const snapshot = objectAt(value, endpoint, "$", status);
  stringAt(snapshot.session_id, endpoint, "session_id", status, false);
  stringAt(snapshot.player_id, endpoint, "player_id", status, false);
  enumAt(snapshot.status, VERDICT_STATUSES, endpoint, "status", status);
  nullableNumberAt(snapshot.score, endpoint, "score", status);
  nullableNumberAt(snapshot.confidence, endpoint, "confidence", status);
  booleanAt(snapshot.assessment_available, endpoint, "assessment_available", status);
  if (snapshot.final_verdict !== null) validateFinalVerdict(snapshot.final_verdict, endpoint, "final_verdict", status);
  stringArrayAt(snapshot.reason_codes, endpoint, "reason_codes", status);
  enumAt(snapshot.data_state, DATA_STATES, endpoint, "data_state", status);
  arrayAt(snapshot.modules, endpoint, "modules", status).forEach((value, index) => {
    const module = objectAt(value, endpoint, `modules[${index}]`, status);
    stringAt(module.session_id, endpoint, `modules[${index}].session_id`, status, false);
    stringAt(module.player_id, endpoint, `modules[${index}].player_id`, status, false);
    stringAt(module.module, endpoint, `modules[${index}].module`, status, false);
    finiteNumberAt(module.timestamp_ms, endpoint, `modules[${index}].timestamp_ms`, status);
    nonNegativeIntegerAt(module.sequence, endpoint, `modules[${index}].sequence`, status);
    stringAt(module.event_id, endpoint, `modules[${index}].event_id`, status, false);
    finiteNumberAt(module.raw_score, endpoint, `modules[${index}].raw_score`, status);
    objectAt(module.evidence, endpoint, `modules[${index}].evidence`, status);
    stringArrayAt(module.reasons, endpoint, `modules[${index}].reasons`, status);
  });
  objectAt(snapshot.policy, endpoint, "policy", status);
  return snapshot as unknown as SnapshotResponse;
}

function decodeGodModeHistory(value: unknown, status: number): GodModeHistoryResponse {
  const endpoint = "godmode history";
  const response = objectAt(value, endpoint, "$", status);
  arrayAt(response.items, endpoint, "items", status).forEach((value, index) => {
    const item = objectAt(value, endpoint, `items[${index}]`, status);
    stringAt(item.event_id, endpoint, `items[${index}].event_id`, status, false);
    nonNegativeIntegerAt(item.sequence, endpoint, `items[${index}].sequence`, status);
    stringAt(item.session_id, endpoint, `items[${index}].session_id`, status, false);
    stringAt(item.player_id, endpoint, `items[${index}].player_id`, status, false);
    const module = stringAt(item.module, endpoint, `items[${index}].module`, status, false);
    if (module !== "godmode") invalidContract(endpoint, `items[${index}].module`, status);
    finiteNumberAt(item.timestamp_ms, endpoint, `items[${index}].timestamp_ms`, status);
    finiteNumberAt(item.raw_score, endpoint, `items[${index}].raw_score`, status);
    objectAt(item.evidence, endpoint, `items[${index}].evidence`, status);
    stringArrayAt(item.reasons, endpoint, `items[${index}].reasons`, status);
  });
  booleanAt(response.has_more, endpoint, "has_more", status);
  if (response.next_after_sequence !== null) {
    nonNegativeIntegerAt(response.next_after_sequence, endpoint, "next_after_sequence", status);
  }
  if (response.final_assessment !== false) {
    invalidContract(endpoint, "final_assessment", status);
  }
  return response as unknown as GodModeHistoryResponse;
}

function decodeStatus(value: unknown, status: number): SubjectStatusResponse {
  const endpoint = "status";
  const response = objectAt(value, endpoint, "$", status);
  stringAt(response.session_id, endpoint, "session_id", status, false);
  stringAt(response.player_id, endpoint, "player_id", status, false);
  enumAt(response.state, CONNECTION_STATES, endpoint, "state", status);
  arrayAt(response.sources, endpoint, "sources", status).forEach((source, index) => {
    validateStatusSource(source, endpoint, `sources[${index}]`, status);
  });
  validateLauncher(response.launcher, endpoint, "launcher", status);
  if (response.has_more_sources !== undefined) {
    booleanAt(response.has_more_sources, endpoint, "has_more_sources", status);
  } else {
    response.has_more_sources = false;
  }
  stringAt(response.reason, endpoint, "reason", status);
  return response as unknown as SubjectStatusResponse;
}

function cleanConnection(input: LiveConnectionInput): Required<Pick<LiveConnectionInput, "baseUrl" | "token">> {
  const baseUrl = input.baseUrl.trim().replace(/\/+$/, "");
  const token = input.token.trim();
  if (!baseUrl || !token) {
    throw new DashboardApiError("서버 주소와 Dashboard Bearer 토큰을 모두 입력해주세요.");
  }
  return { baseUrl, token };
}

function queryString(values: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

function abortError(): Error {
  if (typeof DOMException !== "undefined") {
    return new DOMException("Dashboard request was cancelled.", "AbortError");
  }
  const error = new Error("Dashboard request was cancelled.");
  error.name = "AbortError";
  return error;
}

async function requestJson<T>(
  input: LiveConnectionInput,
  path: string,
  decode: (value: unknown, status: number) => T,
  signal?: AbortSignal,
): Promise<T> {
  const { baseUrl, token } = cleanConnection(input);
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(signal?.reason);
  if (signal?.aborted) throw abortError();
  signal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, REQUEST_TIMEOUT_MS);

  try {
    let response: Response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        method: "GET",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
        },
        cache: "no-store",
        signal: controller.signal,
      });
    } catch (error) {
      if (signal?.aborted) throw abortError();
      const reason = timedOut || (error instanceof Error && error.name === "AbortError")
        ? "Dashboard 요청 시간이 초과되었습니다."
        : "Dashboard 서버에 연결할 수 없습니다.";
      throw new DashboardApiError(reason);
    }

    if (!response.ok) {
      const messages: Record<number, string> = {
        401: "Dashboard 인증 정보가 올바르지 않습니다.",
        403: "이 Dashboard 데이터에 접근할 권한이 없습니다.",
        404: "요청한 Dashboard 데이터가 없습니다.",
        422: "Dashboard 조회 조건 또는 cursor가 올바르지 않습니다.",
        502: "중앙 서버 응답 형식을 확인할 수 없습니다.",
        503: "Dashboard 데이터 저장소를 현재 사용할 수 없습니다.",
      };
      throw new DashboardApiError(
        messages[response.status] ?? `Dashboard 요청이 실패했습니다. (HTTP ${response.status})`,
        response.status,
      );
    }

    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.toLocaleLowerCase().includes("application/json")) {
      throw new DashboardApiError("Dashboard 서버가 JSON이 아닌 응답을 반환했습니다.", response.status);
    }

    let value: unknown;
    try {
      value = await response.json();
    } catch (error) {
      if (signal?.aborted) throw abortError();
      if (timedOut || (error instanceof Error && error.name === "AbortError")) {
        throw new DashboardApiError("Dashboard 요청 시간이 초과되었습니다.");
      }
      throw new DashboardApiError("Dashboard 서버의 JSON 응답을 해석할 수 없습니다.", response.status);
    }
    return decode(value, response.status);
  } finally {
    globalThis.clearTimeout(timeout);
    signal?.removeEventListener("abort", abortFromCaller);
  }
}

export async function fetchOverview(
  input: LiveConnectionInput,
  options: { sessionId?: string; playerId?: string; afterSession?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<OverviewResponse> {
  const hasSession = Boolean(options.sessionId);
  const hasPlayer = Boolean(options.playerId);
  if (hasSession !== hasPlayer) {
    throw new DashboardApiError("overview의 세션과 플레이어 ID는 함께 지정해야 합니다.");
  }
  const query = queryString({
    session_id: options.sessionId,
    player_id: options.playerId,
    after_session: options.afterSession,
    limit: options.limit ?? 100,
  });
  return requestJson(input, `/api/dashboard/overview${query}`, decodeOverview, signal);
}

export async function fetchEvents(
  input: LiveConnectionInput,
  options: EventQuery = {},
  signal?: AbortSignal,
): Promise<EventListResponse> {
  if (options.cursor && options.afterSequence) {
    throw new DashboardApiError("events 조회에는 cursor와 after_sequence를 동시에 사용할 수 없습니다.");
  }
  const query = queryString({
    session_id: options.sessionId,
    player_id: options.playerId,
    module: options.module,
    submodule: options.submodule,
    q: options.query,
    cursor: options.cursor,
    after_sequence: options.afterSequence,
    limit: options.limit ?? 200,
  });
  return requestJson(input, `/api/dashboard/events${query}`, decodeEvents, signal);
}

export function fetchEventDetail(
  input: LiveConnectionInput,
  eventId: string,
  signal?: AbortSignal,
): Promise<DashboardEvent> {
  if (!eventId.trim()) throw new DashboardApiError("event ID가 비어 있습니다.");
  return requestJson(
    input,
    `/api/dashboard/events/${encodeURIComponent(eventId)}`,
    decodeEvent,
    signal,
  );
}

export function fetchSnapshot(
  input: LiveConnectionInput,
  sessionId: string,
  playerId: string,
  signal?: AbortSignal,
): Promise<SnapshotResponse> {
  if (!sessionId.trim() || !playerId.trim()) {
    throw new DashboardApiError("snapshot 조회에는 세션과 플레이어 ID가 필요합니다.");
  }
  return requestJson(
    input,
    `/api/dashboard/sessions/${encodeURIComponent(sessionId)}/players/${encodeURIComponent(playerId)}/snapshot`,
    decodeSnapshot,
    signal,
  );
}

export function fetchSubjectStatus(
  input: LiveConnectionInput,
  sessionId: string,
  playerId: string,
  signal?: AbortSignal,
): Promise<SubjectStatusResponse> {
  if (!sessionId.trim() || !playerId.trim()) {
    throw new DashboardApiError("status 조회에는 세션과 플레이어 ID가 필요합니다.");
  }
  return requestJson(
    input,
    `/api/dashboard/sessions/${encodeURIComponent(sessionId)}/players/${encodeURIComponent(playerId)}/status`,
    decodeStatus,
    signal,
  );
}

export function fetchGodModeHistory(
  input: LiveConnectionInput,
  sessionId: string,
  playerId: string,
  options: { afterSequence?: number; limit?: number } = {},
  signal?: AbortSignal,
): Promise<GodModeHistoryResponse> {
  if (!sessionId.trim() || !playerId.trim()) {
    throw new DashboardApiError("GodMode 이력 조회에는 세션과 플레이어 ID가 필요합니다.");
  }
  const query = queryString({
    module: "godmode",
    after_sequence: options.afterSequence,
    limit: options.limit ?? 100,
  });
  return requestJson(
    input,
    `/api/dashboard/sessions/${encodeURIComponent(sessionId)}/players/${encodeURIComponent(playerId)}/history${query}`,
    decodeGodModeHistory,
    signal,
  );
}

function paginationState(
  pagesLoaded: number,
  itemsLoaded: number,
  truncationReason: PaginationLoadState["truncation_reason"],
): PaginationLoadState {
  return {
    pages_loaded: pagesLoaded,
    items_loaded: itemsLoaded,
    complete: truncationReason === null,
    truncated: truncationReason !== null,
    truncation_reason: truncationReason,
  };
}

function mergedLauncherConnection(
  firstPage: OverviewResponse,
  pages: OverviewResponse[],
  launcherStatuses: OverviewResponse["launcher_statuses"],
): OverviewResponse["connection"]["Launcher"] {
  const stateCounts: Record<string, number> = {};
  for (const item of launcherStatuses) stateCounts[item.state] = (stateCounts[item.state] ?? 0) + 1;
  const states = new Set(Object.keys(stateCounts));
  let state: OverviewResponse["connection"]["Launcher"]["state"];
  if (states.size === 0 || (states.size === 1 && states.has("unknown"))) state = "unknown";
  else if (states.size === 1) {
    const only = [...states][0]!;
    state = only === "healthy" ? "online" : only === "failed" ? "degraded" : only as typeof state;
  } else if ([...states].every((value) => ["stale", "stopped", "unknown"].includes(value)) && states.has("stale")) state = "stale";
  else state = "degraded";

  const lastPage = pages.at(-1) ?? firstPage;
  const checkedAt = lastPage.connection.Launcher.checked_at_utc ?? firstPage.connection.Launcher.checked_at_utc;
  return {
    ...firstPage.connection.Launcher,
    state,
    scope: pages.length > 1 ? "all_returned_sessions" : firstPage.connection.Launcher.scope,
    ...(checkedAt !== undefined ? { checked_at_utc: checkedAt } : {}),
    state_counts: stateCounts,
    observed_pairs: launcherStatuses.length,
    connected_pairs: launcherStatuses.reduce((count, item) => count + (item.connected ? 1 : 0), 0),
  };
}

function mergeOverviewPages(firstPage: OverviewResponse, pages: OverviewResponse[]): OverviewResponse {
  const sessions = new Map(firstPage.sessions.map((item) => [item.id, item]));
  const players = new Map(firstPage.players.map((item) => [item.id, item]));
  const assessments = new Map(firstPage.assessments.map((item) => [item.id, item]));
  const moduleStatuses = new Map(firstPage.module_statuses.map((item) => [item.status_id, item]));
  const launcherStatuses = new Map(
    firstPage.launcher_statuses.map((item) => [`${item.session_id}:${item.player_id}`, item]),
  );

  for (const page of pages) {
    for (const item of page.sessions) {
      const previous = sessions.get(item.id);
      sessions.set(item.id, previous ? {
        ...previous,
        player_ids: [...new Set([...previous.player_ids, ...item.player_ids])],
        module_ids: [...new Set([...previous.module_ids, ...item.module_ids])],
      } : item);
    }
    for (const item of page.players) {
      const previous = players.get(item.id);
      players.set(item.id, previous ? {
        ...previous,
        session_ids: [...new Set([...previous.session_ids, ...item.session_ids])],
      } : item);
    }
    for (const item of page.assessments) if (!assessments.has(item.id)) assessments.set(item.id, item);
    for (const item of page.module_statuses) if (!moduleStatuses.has(item.status_id)) moduleStatuses.set(item.status_id, item);
    for (const item of page.launcher_statuses) {
      const id = `${item.session_id}:${item.player_id}`;
      if (!launcherStatuses.has(id)) launcherStatuses.set(id, item);
    }
  }

  const lastPage = pages.at(-1) ?? firstPage;
  const mergedLauncherStatuses = [...launcherStatuses.values()];
  return {
    ...firstPage,
    connection: {
      ...firstPage.connection,
      Launcher: mergedLauncherConnection(firstPage, [firstPage, ...pages], mergedLauncherStatuses),
    },
    sessions: [...sessions.values()],
    players: [...players.values()],
    assessments: [...assessments.values()],
    module_statuses: [...moduleStatuses.values()],
    launcher_statuses: mergedLauncherStatuses,
    session_page: lastPage.session_page,
  };
}

/**
 * Load the two list endpoints used by the main screen. This is live-only: a
 * failed API call is surfaced to the caller and is never replaced by demo data.
 */
export async function loadDashboardBundle(
  input: LiveConnectionInput,
  filters?: Partial<DashboardFilters>,
  afterSequence = 0,
  signal?: AbortSignal,
): Promise<DashboardBundle> {
  const sessionId = filters?.sessionId && filters.sessionId !== "ALL" ? filters.sessionId : undefined;
  const playerId = filters?.playerId && filters.playerId !== "ALL" ? filters.playerId : undefined;
  const module = filters?.module && filters.module !== "ALL" ? filters.module : undefined;
  const submodule = filters?.submodule && filters.submodule !== "ALL" ? filters.submodule : undefined;
  const query = filters?.query?.trim() || undefined;

  // The overview endpoint requires session/player as a pair. An incomplete
  // UI selection still applies to events but must not send an invalid overview.
  const overviewScope = sessionId && playerId ? { sessionId, playerId } : {};
  const [firstOverviewPage, firstPage] = await Promise.all([
    fetchOverview(input, overviewScope, signal),
    fetchEvents(input, { sessionId, playerId, module, submodule, query, afterSequence, limit: 200 }, signal),
  ]);

  const overviewPages = [firstOverviewPage];
  const seenOverviewCursors = new Set<string>();
  let overviewPage = firstOverviewPage;
  let overviewTruncation: PaginationLoadState["truncation_reason"] = null;
  while (overviewPage.session_page.has_more) {
    const cursor = overviewPage.session_page.next_after_session;
    if (!cursor) {
      overviewTruncation = "missing_cursor";
      break;
    }
    if (seenOverviewCursors.has(cursor)) {
      overviewTruncation = "repeated_cursor";
      break;
    }
    if (overviewPages.length >= MAX_OVERVIEW_PAGES) {
      overviewTruncation = "page_limit";
      break;
    }
    seenOverviewCursors.add(cursor);
    overviewPage = await fetchOverview(input, { ...overviewScope, afterSession: cursor }, signal);
    overviewPages.push(overviewPage);
  }
  const overview = mergeOverviewPages(firstOverviewPage, overviewPages.slice(1));

  const itemsById = new Map(firstPage.items.map((item) => [item.id, item]));
  let page = firstPage;
  let pages = 1;
  const seenEventCursors = new Set<string>();
  let eventTruncation: PaginationLoadState["truncation_reason"] = null;
  while (page.has_more) {
    const cursor = page.next_cursor;
    if (!cursor) {
      eventTruncation = "missing_cursor";
      break;
    }
    if (seenEventCursors.has(cursor)) {
      eventTruncation = "repeated_cursor";
      break;
    }
    if (pages >= MAX_EVENT_PAGES) {
      eventTruncation = "page_limit";
      break;
    }
    seenEventCursors.add(cursor);
    page = await fetchEvents(input, { sessionId, playerId, module, submodule, query, cursor, limit: 200 }, signal);
    for (const item of page.items) if (!itemsById.has(item.id)) itemsById.set(item.id, item);
    pages += 1;
  }

  const items = [...itemsById.values()];

  return {
    overview,
    events: {
      ...page,
      items,
      has_more: page.has_more,
      next_cursor: page.next_cursor,
      through_sequence: firstPage.through_sequence,
      index: firstPage.index,
    },
    load_state: {
      overview: paginationState(overviewPages.length, overview.sessions.length, overviewTruncation),
      events: paginationState(pages, items.length, eventTruncation),
    },
  };
}

export async function loadSubjectDetail(
  input: LiveConnectionInput,
  sessionId: string,
  playerId: string,
  signal?: AbortSignal,
): Promise<SubjectDetail> {
  const requests = loadSubjectDetailParts(input, sessionId, playerId, signal);
  const [snapshot, status] = await Promise.all([requests.snapshot, requests.status]);
  return { snapshot, status };
}

/**
 * Start the scoring snapshot and runtime-status reads independently.
 *
 * A missing Scoring snapshot must not prevent a caller from rendering a valid
 * Launcher heartbeat (and vice versa). New UI call sites should handle these
 * promises separately. `loadSubjectDetail` remains as the all-or-nothing
 * compatibility wrapper for existing consumers.
 */
export function loadSubjectDetailParts(
  input: LiveConnectionInput,
  sessionId: string,
  playerId: string,
  signal?: AbortSignal,
): {
  snapshot: Promise<SnapshotResponse>;
  status: Promise<SubjectStatusResponse>;
} {
  return {
    snapshot: fetchSnapshot(input, sessionId, playerId, signal),
    status: fetchSubjectStatus(input, sessionId, playerId, signal),
  };
}

export function loadEventDetail(
  input: LiveConnectionInput,
  eventId: string,
  signal?: AbortSignal,
): Promise<DashboardEvent> {
  return fetchEventDetail(input, eventId, signal);
}
