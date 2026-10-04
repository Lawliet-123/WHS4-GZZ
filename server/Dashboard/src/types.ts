/**
 * Browser-side representation of the 8-B Dashboard backend v2 contract.
 *
 * The backend is the authority for verdicts. `score` and `confidence` are
 * nullable because conservative-v1 does not publish either value. The UI must
 * not manufacture them from detector `raw_score` values.
 */

export type FinalAssessmentStatus =
  | "SUSPICIOUS"
  | "INCONCLUSIVE"
  | "NO_ACTIVE_EVIDENCE";

export type VerdictStatus = FinalAssessmentStatus | "UNKNOWN";

/** Kept as an alias for components that used the previous name. */
export type FinalVerdictStatus = VerdictStatus;

export type DataState = "available" | "missing" | "not_connected";

export type ComponentStatus =
  | "starting"
  | "running"
  | "healthy"
  | "degraded"
  | "failed"
  | "stopped"
  | "stale"
  | "unknown";

export type ConnectionState =
  | "online"
  | "unavailable"
  | "starting"
  | "stopping"
  | "healthy"
  | "degraded"
  | "failed"
  | "stopped"
  | "stale"
  | "unknown";

export type DashboardEventKind = "detection" | "operational";

export interface FinalVerdict {
  version: string;
  session_id: string;
  player_id: string;
  status: FinalAssessmentStatus;
  assessment_complete: boolean;
  evidence_unit_count: number;
  active_module_count: number;
  overlap_adjustment_count: number;
  active_modules: string[];
  advisory_modules: string[];
  unresolved_modules: string[];
  deferred_modules: string[];
  unavailable_modules: string[];
  reason_codes: string[];
}

export interface DashboardEvent {
  id: string;
  sequence: number;
  session_id: string;
  player_id: string;
  module: string;
  timestamp_ms: number;
  evidence: Record<string, unknown>;
  reasons: string[];
  raw_score: number;
  event_kind: DashboardEventKind;
  time_basis: "unknown" | string;
  evidence_image: string | null;
  log_excerpt: string | null;
}

/** Previous UI name retained while components move to the v2 name. */
export type DetectionEvent = DashboardEvent;

export interface EventIndexState {
  through_sequence: number;
  catching_up: boolean;
}

export interface EventListResponse {
  items: DashboardEvent[];
  next_cursor: string | null;
  has_more: boolean;
  through_sequence: number;
  index: EventIndexState;
}

export type PaginationTruncationReason =
  | "page_limit"
  | "missing_cursor"
  | "repeated_cursor";

/**
 * Client-side collection state. The backend responses stay unchanged; this
 * metadata tells the screen whether its merged view is complete.
 */
export interface PaginationLoadState {
  pages_loaded: number;
  items_loaded: number;
  complete: boolean;
  truncated: boolean;
  truncation_reason: PaginationTruncationReason | null;
}

export interface OverviewCounts {
  scope: "indexed_events" | string;
  events: number;
  sessions: number;
  players: number;
  operational_events: number;
  review: number | null;
  high: number | null;
}

export interface OverviewSession {
  id: string;
  player_ids: string[];
  module_ids: string[];
  duration_ms: number | null;
  max_observed_timestamp_ms: number | null;
  status: VerdictStatus;
  score: number | null;
}

export interface OverviewPlayer {
  id: string;
  display_name: string;
  identity_type: string;
  session_ids: string[];
  status: VerdictStatus;
  max_score: number | null;
}

export interface Assessment {
  id: string;
  session_id: string;
  player_id: string;
  status: VerdictStatus;
  assessment_available: boolean;
  score: number | null;
  confidence: number | null;
  final_verdict: FinalVerdict | null;
  reason_codes: string[];
  data_state: DataState;
  module_scores: unknown[];
  reasons: string[];
}

export interface ConnectionProbe {
  state: ConnectionState;
  scope: string;
  checked_at_utc?: string;
  state_counts?: Record<string, number>;
  observed_pairs?: number;
  connected_pairs?: number;
}

export interface DashboardCapabilities {
  final_assessment: boolean;
  launcher_heartbeat: boolean;
  evidence_images: boolean;
  heartbeat_query: boolean;
}

export interface ModuleStatus {
  id: string;
  label: string;
  status_id: string;
  session_id: string;
  player_id: string;
  client_id: string;
  state: ComponentStatus;
  reported_status: ComponentStatus;
  required: boolean;
  pid: number | null;
  updated_at_ms: number;
  stale_after_ms: number;
  age_ms: number;
  effective_age_ms: number;
  last_seen_at: string;
  details: Record<string, unknown>;
}

export interface TransportStatus {
  configured: boolean;
  consecutive_failures: number;
  last_success_sequence: number | null;
  last_error_type: string | null;
}

export interface StatusComponent {
  id: string;
  state: ComponentStatus;
  reported_status: ComponentStatus;
  status?: ComponentStatus;
  required: boolean;
  pid: number | null;
  updated_at_ms: number;
  stale_after_ms: number;
  age_ms: number;
  effective_age_ms: number;
  details: Record<string, unknown>;
}

export interface StatusSource {
  client_id: string;
  role: "launcher" | "component";
  sequence: number;
  received_at_utc: string;
  age_ms: number;
  state: ComponentStatus;
  reported_status: ComponentStatus;
  components: StatusComponent[];
  transport: TransportStatus;
}

export interface LauncherStatus {
  state: ConnectionState;
  connected: boolean;
  source: StatusSource | null;
  reason: string;
}

export interface LauncherOverviewStatus extends LauncherStatus {
  session_id: string;
  player_id: string;
}

export interface OverviewResponse {
  schema_version: "dashboard-v0" | string;
  generated_at_utc: string;
  capabilities: DashboardCapabilities;
  connection: {
    Receiver: ConnectionProbe;
    Scoring: ConnectionProbe;
    Launcher: ConnectionProbe;
  };
  counts: OverviewCounts;
  sessions: OverviewSession[];
  players: OverviewPlayer[];
  assessments: Assessment[];
  session_page: {
    next_after_session: string | null;
    has_more: boolean;
  };
  events: DashboardEvent[];
  module_statuses: ModuleStatus[];
  launcher_statuses: LauncherOverviewStatus[];
  events_endpoint: string;
  index: EventIndexState;
}

export interface ModuleSnapshot {
  session_id: string;
  player_id: string;
  module: string;
  timestamp_ms: number;
  sequence: number;
  event_id: string;
  raw_score: number;
  evidence: Record<string, unknown>;
  reasons: string[];
}

export interface SnapshotResponse {
  session_id: string;
  player_id: string;
  status: VerdictStatus;
  score: number | null;
  confidence: number | null;
  assessment_available: boolean;
  final_verdict: FinalVerdict | null;
  reason_codes: string[];
  data_state: DataState;
  modules: ModuleSnapshot[];
  policy: Record<string, unknown>;
}

export interface SubjectStatusResponse {
  session_id: string;
  player_id: string;
  state: ConnectionState;
  sources: StatusSource[];
  launcher: LauncherStatus;
  has_more_sources: boolean;
  reason: string;
}

export interface DashboardBundle {
  overview: OverviewResponse;
  events: EventListResponse;
  load_state: {
    overview: PaginationLoadState;
    events: PaginationLoadState;
  };
}

export interface SubjectDetail {
  snapshot: SnapshotResponse;
  status: SubjectStatusResponse;
}

export interface LiveConnectionInput {
  baseUrl: string;
  token: string;
  sessionId?: string;
  playerId?: string;
}

export interface DashboardFilters {
  sessionId: string;
  playerId: string;
  module: string;
  submodule: string;
  verdict: "ALL" | VerdictStatus;
  query: string;
}

export interface EventQuery {
  sessionId?: string;
  playerId?: string;
  module?: string;
  submodule?: string;
  query?: string;
  cursor?: string;
  afterSequence?: number;
  limit?: number;
}

export interface TimelineBucket {
  startMs: number;
  endMs: number;
  total: number;
  detections: number;
  operational: number;
}
