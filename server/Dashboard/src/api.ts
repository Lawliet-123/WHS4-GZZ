import type {
  DashboardBundle,
  DashboardEvent,
  DashboardFilters,
  EventListResponse,
  EventQuery,
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

export class DashboardApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "DashboardApiError";
    this.status = status;
  }
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

async function requestJson<T>(input: LiveConnectionInput, path: string, signal?: AbortSignal): Promise<T> {
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
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      throw new DashboardApiError("Dashboard 서버의 응답 형식이 올바르지 않습니다.", response.status);
    }
    return value as T;
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
  return requestJson<OverviewResponse>(input, `/api/dashboard/overview${query}`, signal);
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
  return requestJson<EventListResponse>(input, `/api/dashboard/events${query}`, signal);
}

export function fetchEventDetail(
  input: LiveConnectionInput,
  eventId: string,
  signal?: AbortSignal,
): Promise<DashboardEvent> {
  if (!eventId.trim()) throw new DashboardApiError("event ID가 비어 있습니다.");
  return requestJson<DashboardEvent>(
    input,
    `/api/dashboard/events/${encodeURIComponent(eventId)}`,
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
  return requestJson<SnapshotResponse>(
    input,
    `/api/dashboard/sessions/${encodeURIComponent(sessionId)}/players/${encodeURIComponent(playerId)}/snapshot`,
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
  return requestJson<SubjectStatusResponse>(
    input,
    `/api/dashboard/sessions/${encodeURIComponent(sessionId)}/players/${encodeURIComponent(playerId)}/status`,
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
  const [snapshot, status] = await Promise.all([
    fetchSnapshot(input, sessionId, playerId, signal),
    fetchSubjectStatus(input, sessionId, playerId, signal),
  ]);
  return { snapshot, status };
}

export function loadEventDetail(
  input: LiveConnectionInput,
  eventId: string,
  signal?: AbortSignal,
): Promise<DashboardEvent> {
  return fetchEventDetail(input, eventId, signal);
}
