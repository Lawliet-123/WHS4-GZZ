import type {
  Assessment,
  DashboardEvent,
  EventListResponse,
  FinalAssessmentStatus,
  FinalVerdict,
  ModuleSnapshot,
  OverviewResponse,
  SnapshotResponse,
  StatusComponent,
  StatusSource,
  SubjectStatusResponse,
} from "./types";

/**
 * Synthetic records for UI development only. They deliberately use the exact
 * backend-v2 response envelopes so demo and live rendering share one code path.
 */

const generatedAt = "2026-10-05T03:20:00+00:00";

function makeVerdict(
  sessionId: string,
  playerId: string,
  status: FinalAssessmentStatus,
  changes: Partial<FinalVerdict> = {},
): FinalVerdict {
  return {
    version: "conservative-v1",
    session_id: sessionId,
    player_id: playerId,
    status,
    assessment_complete: status !== "INCONCLUSIVE",
    evidence_unit_count: 0,
    active_module_count: 0,
    overlap_adjustment_count: 0,
    active_modules: [],
    advisory_modules: [],
    unresolved_modules: [],
    deferred_modules: [],
    unavailable_modules: [],
    reason_codes: [],
    ...changes,
  };
}

function makeAssessment(
  sessionId: string,
  playerId: string,
  verdict: FinalVerdict | null,
): Assessment {
  return {
    id: `${sessionId}:${playerId}`,
    session_id: sessionId,
    player_id: playerId,
    status: verdict?.status ?? "UNKNOWN",
    assessment_available: verdict !== null,
    score: null,
    confidence: null,
    final_verdict: verdict,
    reason_codes: verdict?.reason_codes ?? [],
    data_state: verdict ? "available" : "missing",
    module_scores: [],
    reasons: [],
  };
}

const longAimbotPlayerId = "BP_FirstPersonCharacter_Hunter_Default_C_2147479755";

const espVerdict = makeVerdict("demo_esp_001", "player_042", "SUSPICIOUS", {
  assessment_complete: false,
  evidence_unit_count: 2,
  active_module_count: 1,
  overlap_adjustment_count: 1,
  active_modules: ["esp"],
  advisory_modules: ["external_access"],
  unresolved_modules: ["module_integrity"],
  reason_codes: ["CALIBRATED_ACTIVE_EVIDENCE", "ASSESSMENT_INCOMPLETE", "ADVISORY_EVIDENCE_PRESENT"],
});

const normalVerdict = makeVerdict("demo_normal_001", "player_007", "NO_ACTIVE_EVIDENCE", {
  reason_codes: ["NO_ACTIVE_EVIDENCE"],
});

const noclipVerdict = makeVerdict("demo_noclip_001", "player_013", "INCONCLUSIVE", {
  assessment_complete: false,
  unresolved_modules: ["noclip"],
  reason_codes: ["ASSESSMENT_INCOMPLETE"],
});

const aimbotVerdict = makeVerdict("demo_aimbot_001", longAimbotPlayerId, "SUSPICIOUS", {
  evidence_unit_count: 1,
  active_module_count: 1,
  active_modules: ["aimbot"],
  reason_codes: ["CALIBRATED_ACTIVE_EVIDENCE"],
});

const event = (
  sequence: number,
  input: Omit<DashboardEvent, "id" | "sequence" | "event_kind" | "time_basis" | "evidence_image" | "log_excerpt"> &
    Partial<Pick<DashboardEvent, "event_kind" | "evidence_image" | "log_excerpt">>,
): DashboardEvent => ({
  id: `00000000-0000-4000-8000-${String(sequence).padStart(12, "0")}`,
  sequence,
  event_kind: "detection",
  time_basis: "unknown",
  evidence_image: null,
  log_excerpt: null,
  ...input,
});

const eventItems: DashboardEvent[] = [
  event(1, {
    session_id: "demo_normal_001",
    player_id: "player_007",
    module: "module_integrity",
    timestamp_ms: 18_000,
    raw_score: 0,
    reasons: [],
    evidence: {
      synthetic: true,
      event_type: "module_inventory",
      observed_modules: 136,
      changes: { added: 0, removed: 0, changed: 0 },
    },
  }),
  event(2, {
    session_id: "demo_noclip_001",
    player_id: "player_013",
    module: "noclip",
    timestamp_ms: 82_000,
    raw_score: 2,
    reasons: ["Collision Disabled"],
    evidence: { synthetic: true, collision: 0, blocked_path: 0, duration_ms: 4_000 },
  }),
  event(3, {
    session_id: "demo_noclip_001",
    player_id: "player_013",
    module: "noclip",
    timestamp_ms: 91_000,
    raw_score: 3,
    reasons: ["Collision Disabled Too Long", "Blocked Path Detected"],
    evidence: { synthetic: true, collision: 0, blocked_path: 1, path: { trace_count: 3, blocking_hits: 1 } },
  }),
  event(4, {
    session_id: "demo_esp_001",
    player_id: "player_042",
    module: "external_access",
    timestamp_ms: 60_163,
    raw_score: 3,
    reasons: ["external process requested game-memory modification or remote-thread rights"],
    evidence: {
      synthetic: true,
      submodule: "external_process",
      event_type: "process_access",
      access_labels: ["CREATE_THREAD", "VM_OPERATION", "VM_READ", "VM_WRITE"],
      granted_access_hex: "0x001F3FFF",
      signature: { status: "trusted", backend: "WinVerifyTrust" },
    },
  }),
  event(5, {
    session_id: "demo_esp_001",
    player_id: "player_042",
    module: "esp",
    timestamp_ms: 66_282,
    raw_score: 3,
    reasons: ["Overlay activity correlated", "PROCESS_VM_READ handle active"],
    evidence: {
      synthetic: true,
      submodule: "overlay_correlation",
      overlapping_window_count: 1,
      process_access: { vm_read: true, source_signature: "trusted" },
    },
  }),
  // Deliberately repeats the elapsed timestamp. Server event IDs and sequences,
  // not timestamp_ms, are the stable identity.
  event(6, {
    session_id: "demo_aimbot_001",
    player_id: longAimbotPlayerId,
    module: "aimbot",
    timestamp_ms: 84_438,
    raw_score: 0,
    reasons: [],
    evidence: { synthetic: true, shot_attempt_count: 2, confirmed_outcome_count: 0, success_rate: 0 },
  }),
  event(7, {
    session_id: "demo_aimbot_001",
    player_id: longAimbotPlayerId,
    module: "aimbot",
    timestamp_ms: 84_438,
    raw_score: 4,
    reasons: ["Consistent Target Convergence Before Confirmed Find", "Target Lock Maintained Before Confirmed Find"],
    evidence: {
      synthetic: true,
      shot_attempt_count: 2,
      confirmed_outcome_count: 1,
      aim: { sample_count: 115, mean_final_error_deg: 0.001, lock_duration_ms: 280 },
    },
  }),
  event(8, {
    session_id: "demo_esp_001",
    player_id: "player_042",
    module: "selfdefense",
    timestamp_ms: 70_000,
    raw_score: 0,
    reasons: [],
    event_kind: "operational",
    evidence: { synthetic: true, kind: "module_health", status: "RUNNING" },
  }),
];

const assessments = [
  makeAssessment("demo_aimbot_001", longAimbotPlayerId, aimbotVerdict),
  makeAssessment("demo_esp_001", "player_042", espVerdict),
  makeAssessment("demo_noclip_001", "player_013", noclipVerdict),
  makeAssessment("demo_normal_001", "player_007", normalVerdict),
];

const launcherComponent = (state: StatusComponent["state"] = "running"): StatusComponent => ({
  id: "launcher",
  status: "running",
  reported_status: "running",
  state,
  required: true,
  pid: 6840,
  updated_at_ms: 89_900,
  stale_after_ms: 30_000,
  age_ms: 100,
  effective_age_ms: 250,
  details: { phase: "running", synthetic: true },
});

function sourceFor(sessionId: string, playerId: string, state: "healthy" | "degraded"): StatusSource {
  return {
    client_id: "launcher-1791169900000",
    role: "launcher",
    sequence: 18,
    received_at_utc: generatedAt,
    age_ms: 150,
    state,
    reported_status: state === "degraded" ? "degraded" : "healthy",
    components: [
      launcherComponent(),
      {
        id: sessionId.includes("esp") ? "esp" : sessionId.includes("noclip") ? "noclip" : sessionId.includes("aimbot") ? "aimbot" : "module_integrity",
        status: state === "degraded" ? "degraded" : "running",
        reported_status: state === "degraded" ? "degraded" : "running",
        state: state === "degraded" ? "degraded" : "running",
        required: true,
        pid: 7320,
        updated_at_ms: 89_500,
        stale_after_ms: 30_000,
        age_ms: 500,
        effective_age_ms: 650,
        details: { synthetic: true, player_id: playerId },
      },
    ],
    transport: {
      configured: true,
      consecutive_failures: 0,
      last_success_sequence: 18,
      last_error_type: null,
    },
  };
}

function statusFor(sessionId: string, playerId: string, state: "healthy" | "degraded"): SubjectStatusResponse {
  const source = sourceFor(sessionId, playerId, state);
  return {
    session_id: sessionId,
    player_id: playerId,
    state,
    sources: [source],
    launcher: {
      state,
      connected: !["unknown", "stale", "stopped"].includes(state),
      source,
      reason: state === "degraded" ? "일부 구성 요소 상태가 저하됨" : "",
    },
    has_more_sources: false,
    reason: state === "degraded" ? "일부 구성 요소 상태가 저하됨" : "",
  };
}

export const demoStatuses: Record<string, SubjectStatusResponse> = {
  "demo_aimbot_001::BP_FirstPersonCharacter_Hunter_Default_C_2147479755": statusFor("demo_aimbot_001", longAimbotPlayerId, "healthy"),
  "demo_esp_001::player_042": statusFor("demo_esp_001", "player_042", "degraded"),
  "demo_noclip_001::player_013": statusFor("demo_noclip_001", "player_013", "healthy"),
  "demo_normal_001::player_007": statusFor("demo_normal_001", "player_007", "healthy"),
};

function latestModules(sessionId: string, playerId: string): ModuleSnapshot[] {
  const latest = new Map<string, DashboardEvent>();
  for (const item of eventItems) {
    if (item.session_id !== sessionId || item.player_id !== playerId) continue;
    const previous = latest.get(item.module);
    if (!previous || item.sequence > previous.sequence) latest.set(item.module, item);
  }
  return [...latest.values()].map((item) => ({
    session_id: item.session_id,
    player_id: item.player_id,
    module: item.module,
    timestamp_ms: item.timestamp_ms,
    sequence: item.sequence,
    event_id: item.id,
    raw_score: item.raw_score,
    evidence: item.evidence,
    reasons: item.reasons,
  }));
}

function snapshotFrom(assessment: Assessment): SnapshotResponse {
  return {
    session_id: assessment.session_id,
    player_id: assessment.player_id,
    status: assessment.status,
    score: null,
    confidence: null,
    assessment_available: assessment.assessment_available,
    final_verdict: assessment.final_verdict,
    reason_codes: assessment.reason_codes,
    data_state: assessment.data_state,
    modules: latestModules(assessment.session_id, assessment.player_id),
    policy: {
      session_id: assessment.session_id,
      player_id: assessment.player_id,
      modules: [],
      correlation_candidates: [],
      synthetic: true,
    },
  };
}

export const demoSnapshots: Record<string, SnapshotResponse> = Object.fromEntries(
  assessments.map((assessment) => [
    `${assessment.session_id}::${assessment.player_id}`,
    snapshotFrom(assessment),
  ]),
);

const selectedEspSource = demoStatuses["demo_esp_001::player_042"]!.sources[0]!;

export const demoOverview: OverviewResponse = {
  schema_version: "dashboard-v0",
  generated_at_utc: generatedAt,
  capabilities: {
    final_assessment: true,
    launcher_heartbeat: true,
    evidence_images: false,
    heartbeat_query: true,
  },
  connection: {
    Receiver: { state: "online", scope: "local_detection_storage", checked_at_utc: generatedAt },
    Scoring: { state: "online", scope: "local_scoring_read", checked_at_utc: generatedAt },
    Launcher: {
      state: "degraded",
      scope: "returned_session_page",
      state_counts: { healthy: 3, degraded: 1 },
      observed_pairs: 4,
      connected_pairs: 4,
    },
  },
  counts: {
    scope: "indexed_events",
    events: eventItems.length,
    sessions: 4,
    players: 4,
    operational_events: 1,
    review: null,
    high: null,
  },
  sessions: [
    { id: "demo_aimbot_001", player_ids: [longAimbotPlayerId], module_ids: ["aimbot"], duration_ms: null, max_observed_timestamp_ms: 84_438, status: "UNKNOWN", score: null },
    { id: "demo_esp_001", player_ids: ["player_042"], module_ids: ["esp", "external_access", "selfdefense"], duration_ms: null, max_observed_timestamp_ms: 70_000, status: "UNKNOWN", score: null },
    { id: "demo_noclip_001", player_ids: ["player_013"], module_ids: ["noclip"], duration_ms: null, max_observed_timestamp_ms: 91_000, status: "UNKNOWN", score: null },
    { id: "demo_normal_001", player_ids: ["player_007"], module_ids: ["module_integrity"], duration_ms: null, max_observed_timestamp_ms: 18_000, status: "UNKNOWN", score: null },
  ],
  players: assessments.map((assessment) => ({
    id: assessment.player_id,
    display_name: assessment.player_id,
    identity_type: "client_supplied",
    session_ids: [assessment.session_id],
    status: "UNKNOWN",
    max_score: null,
  })),
  assessments,
  session_page: { next_after_session: null, has_more: false },
  events: [],
  module_statuses: selectedEspSource.components.map((component) => ({
    ...component,
    label: component.id,
    status_id: `demo_esp_001:player_042:${selectedEspSource.client_id}:${component.id}`,
    session_id: "demo_esp_001",
    player_id: "player_042",
    client_id: selectedEspSource.client_id,
    last_seen_at: selectedEspSource.received_at_utc,
  })),
  launcher_statuses: Object.values(demoStatuses).map((status) => ({
    session_id: status.session_id,
    player_id: status.player_id,
    ...status.launcher,
  })),
  events_endpoint: "/api/dashboard/events",
  index: { through_sequence: eventItems.length, catching_up: false },
};

export const demoEvents: EventListResponse = {
  items: eventItems,
  next_cursor: null,
  has_more: false,
  through_sequence: eventItems.length,
  index: { through_sequence: eventItems.length, catching_up: false },
};

export const demoEventItems: DashboardEvent[] = demoEvents.items;
