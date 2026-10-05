import type {
  Assessment,
  DashboardEvent,
  EventListResponse,
  FinalAssessmentStatus,
  FinalVerdict,
  GodModeHistoryResponse,
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

// ESP와 external_access는 현재 Replay calibration이 pending이다. 양수
// 원본 관측이 있더라도 Scoring이 확정한 ACTIVE 근거로 올리지 않는다.
const espVerdict = makeVerdict("demo_esp_001", "player_042", "INCONCLUSIVE", {
  assessment_complete: false,
  unresolved_modules: ["esp", "external_access", "selfdefense"],
  reason_codes: ["ASSESSMENT_INCOMPLETE"],
});

const normalVerdict = makeVerdict("demo_normal_001", "player_007", "NO_ACTIVE_EVIDENCE", {
  reason_codes: ["NO_ACTIVE_EVIDENCE"],
});

// Replay-v1에서 Noclip threshold는 3이다. 최신 유효 관측 raw_score=3은
// calibrated ACTIVE evidence 한 단위가 된다.
const noclipVerdict = makeVerdict("demo_noclip_001", "player_013", "SUSPICIOUS", {
  evidence_unit_count: 1,
  active_module_count: 1,
  active_modules: ["noclip"],
  reason_codes: ["CALIBRATED_ACTIVE_EVIDENCE"],
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
    module: "aimbot",
    timestamp_ms: 18_000,
    raw_score: 0,
    reasons: [],
    evidence: {
      synthetic: true,
      status: "NORMAL",
      round_id: "demo-round-normal",
      shot_attempt_count: 2,
      confirmed_outcome_count: 0,
      success_rate: 0,
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
    timestamp_ms: 58_200,
    raw_score: 0,
    reasons: [],
    evidence: {
      synthetic: true,
      submodule: "module_integrity",
      status: "NORMAL",
      target_process: "PenguinHotel-Win64-Shipping.exe",
      target_pid: 6840,
      scan_duration_ms: 142,
      observed_modules: 136,
      added_modules: 0,
      removed_modules: 0,
      changed_modules: 0,
      allowed_modules: 0,
      baseline_created: false,
      initial_audit_performed: false,
    },
  }),
  event(5, {
    session_id: "demo_esp_001",
    player_id: "player_042",
    module: "external_access",
    timestamp_ms: 60_163,
    raw_score: 7,
    reasons: [
      "External process opened PROCESS_VM_WRITE handle",
      "External process opened PROCESS_VM_OPERATION handle",
      "External process opened PROCESS_CREATE_THREAD handle",
    ],
    evidence: {
      synthetic: true,
      submodule: "external_process",
      source_pid: 9124,
      source_process: "test-observer.exe",
      source_path: "C:\\Program Files\\WHS4 Fixture\\test-observer.exe",
      access_mask: "0x0000002A",
      access_rights: ["PROCESS_VM_WRITE", "PROCESS_VM_OPERATION", "PROCESS_CREATE_THREAD"],
      sha256: "e8d3d86a9b3afc0261e0d93f8f2972259220c51311ef99d03b19c4c0fc4432f0",
      signature_status: "trusted",
      publisher: "CN=WHS4 Test Fixture",
    },
  }),
  event(6, {
    session_id: "demo_esp_001",
    player_id: "player_042",
    module: "esp",
    timestamp_ms: 66_282,
    raw_score: 1,
    reasons: ["external overlay window overlaps the game viewport"],
    evidence: {
      synthetic: true,
      sensor_event_id: "demo-sensor-overlay-001",
      event_type: "window_overlap",
      categories: ["overlay"],
      window_pid: 9124,
      game_pid: 6840,
      hwnd: 740112,
    },
  }),
  // Deliberately repeats the elapsed timestamp. Server event IDs and sequences,
  // not timestamp_ms, are the stable identity.
  event(7, {
    session_id: "demo_aimbot_001",
    player_id: longAimbotPlayerId,
    module: "aimbot",
    timestamp_ms: 84_438,
    raw_score: 0,
    reasons: [],
    evidence: { synthetic: true, shot_attempt_count: 2, confirmed_outcome_count: 0, success_rate: 0 },
  }),
  event(8, {
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
  event(9, {
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

const launcherComponent = (id = "launcher", state: StatusComponent["state"] = "running", required = true): StatusComponent => ({
  id,
  status: state,
  reported_status: state,
  state,
  required,
  pid: state === "unknown" ? null : 6840,
  updated_at_ms: 89_900,
  stale_after_ms: 30_000,
  age_ms: 100,
  effective_age_ms: 250,
  details: { phase: "running", mode: id === "launcher" ? "launcher" : "continuous", synthetic: true },
});

// Launcher registry의 보호·탐지 컴포넌트 이름이다. Event module 이름과
// 일치하지 않는 항목(input_signature, memory_integrity 등)이 있으므로
// 실행 상태와 탐지 채널을 서로 다른 계약으로 유지한다.
const launcherModuleIds = [
  "self_defense",
  "kernel_watcher",
  "external_access",
  "module_integrity",
  "input_signature",
  "memory_integrity",
  "whistle_spoofing",
  "aimbot",
  "esp",
  "godmode",
  "noclip",
  "autopaint",
  "hide_anywhere",
] as const;

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
      ...launcherModuleIds.map((id, index) => {
        const degraded = state === "degraded" && id === "kernel_watcher";
        const component = launcherComponent(id, degraded ? "degraded" : "running");
        return {
          ...component,
          pid: id === "kernel_watcher" ? null : 7_320 + index,
          updated_at_ms: 89_500 - index * 25,
          age_ms: 500 + index * 25,
          effective_age_ms: 650 + index * 25,
          details: {
            synthetic: true,
            player_id: playerId,
            mode: ["memory_integrity", "whistle_spoofing"].includes(id) ? "oneshot" : "continuous",
            ...(degraded ? { launcher_status: "WARN" } : { launcher_status: "RUNNING" }),
          },
        } satisfies StatusComponent;
      }),
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
  return [...latest.values()].map((item) => {
    const base = {
      session_id: item.session_id,
      player_id: item.player_id,
      module: item.module,
      timestamp_ms: item.timestamp_ms,
      sequence: item.sequence,
      event_id: item.id,
      raw_score: item.raw_score,
      evidence: item.evidence,
      reasons: item.reasons,
    };
    if (item.module !== "external_access") return base;

    const scoped = eventItems.filter((candidate) => (
      candidate.session_id === sessionId
      && candidate.player_id === playerId
      && candidate.module === "external_access"
    ));
    return {
      ...base,
      raw_score: Math.max(...scoped.map((candidate) => candidate.raw_score)),
      reasons: [],
      evidence: {
        submodule: "aggregate",
        status: "SUSPICIOUS",
        coverage_complete: true,
        missing_submodules: [],
        unavailable_submodules: [],
        scoped_submodules: Object.fromEntries(scoped.map((candidate) => [
          String(candidate.evidence.submodule),
          {
            raw_score: candidate.raw_score,
            status: candidate.evidence.status ?? null,
            timestamp_ms: candidate.timestamp_ms,
            sequence: candidate.sequence,
            event_id: candidate.id,
          },
        ])),
        derived: true,
      },
    };
  });
}

function policyModule(item: ModuleSnapshot) {
  const reviewedBounds: Record<string, number> = { aimbot: 8, noclip: 5 };
  const emission: Record<string, string> = {
    aimbot: "snapshot",
    noclip: "snapshot",
    esp: "positive_only",
    external_access: "per_entity_positive_only",
  };
  const state = item.module in reviewedBounds
    ? "RAW_FRACTION_ONLY"
    : item.module === "selfdefense"
      ? "UNKNOWN_MODULE"
      : "POLICY_NOT_CALIBRATED";
  const bound = reviewedBounds[item.module];
  const issues = item.module === "esp"
    ? ["no 0-point heartbeat in central feed; last detection is not current health"]
    : item.module === "external_access"
      ? ["multiple source entities require scoped state; silence is not entity recovery"]
      : item.module === "selfdefense"
        ? ["module has no reviewed scoring profile"]
        : [];
  return {
    state: item,
    evaluation: {
      signal: {
        module: item.module,
        raw_score: item.raw_score,
        emission: emission[item.module] ?? "unknown",
        state,
        raw_fraction_pct: bound ? Math.round((item.raw_score / bound) * 100_000) / 1_000 : null,
        issues,
      },
      annotations: {
        entity_key: item.module === "esp"
          ? `overlay_window:${String(item.evidence.window_pid)}:${String(item.evidence.hwnd)}`
          : null,
        overlap_tags: [],
        notes: item.module === "esp" || item.module === "external_access"
          ? ["Replay calibration이 pending이므로 양수 raw_score를 확정 ACTIVE 판정으로 바꾸지 않는다."]
          : [],
      },
    },
  };
}

function snapshotFrom(assessment: Assessment): SnapshotResponse {
  const modules = latestModules(assessment.session_id, assessment.player_id);
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
    modules,
    policy: {
      session_id: assessment.session_id,
      player_id: assessment.player_id,
      modules: modules.map(policyModule),
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

export const demoGodModeHistories: Record<string, GodModeHistoryResponse> = Object.fromEntries(
  assessments.map((assessment) => [
    `${assessment.session_id}::${assessment.player_id}`,
    { items: [], has_more: false, next_after_sequence: null, final_assessment: false },
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
    { id: "demo_normal_001", player_ids: ["player_007"], module_ids: ["aimbot"], duration_ms: null, max_observed_timestamp_ms: 18_000, status: "UNKNOWN", score: null },
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
