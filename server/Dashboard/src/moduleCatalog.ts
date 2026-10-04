import type {
  ComponentStatus,
  DashboardEvent,
  ModuleFilterOption,
  ModuleRollup,
  ModuleRollupState,
  ModuleStatus,
  ProtectionModuleCatalogEntry,
  ProtectionModuleGroup,
  ResolvedProtectionModule,
} from "./types";

/**
 * Logical anti-cheat services registered by client/Launcher/modules.py.
 *
 * A Launcher component name and the Shared Event module name are not always
 * the same. Keeping both identities here prevents the dashboard from showing
 * one service as several unrelated products or silently hiding its Events.
 */
export const protectionModuleCatalog = [
  {
    id: "self_defense",
    label: "Self Defense",
    group: "protection",
    description: "워치독과 자체 무결성으로 탐지기 생존 상태를 감시",
    componentIds: ["self_defense"],
    eventAliases: [{ module: "selfdefense" }],
  },
  {
    id: "kernel_watcher",
    label: "Kernel Watcher",
    group: "protection",
    description: "커널 프로세스와 드라이버 상태를 관측",
    componentIds: ["kernel_watcher"],
    eventAliases: [{ module: "kernel_sentinel" }],
  },
  {
    id: "external_access",
    label: "외부 프로세스 접근",
    group: "local_guard",
    description: "게임 프로세스에 열린 위험 권한 핸들을 감시",
    componentIds: ["external_access"],
    // module_integrity uses the same Shared module and is resolved first by
    // its more-specific submodule alias below.
    eventAliases: [{ module: "external_access" }],
  },
  {
    id: "module_integrity",
    label: "게임 모듈 무결성",
    group: "local_guard",
    description: "게임 DLL 기준선과 이후 추가·변경을 감시",
    componentIds: ["module_integrity"],
    eventAliases: [{ module: "external_access", submodules: ["module_integrity"] }],
  },
  {
    id: "input_signature",
    label: "입력·시그니처",
    group: "local_guard",
    description: "Raw Input, YARA 시그니처와 실행 파일 해시를 검사",
    componentIds: ["input_signature"],
    eventAliases: [
      { module: "localguard_yara" },
      { module: "localguard_executable_hash" },
    ],
  },
  {
    id: "memory_integrity",
    label: "메모리·코드 무결성",
    group: "local_guard",
    description: "값 변조, 코드 패치와 런타임 후킹 흔적을 검사",
    componentIds: ["memory_integrity"],
    eventAliases: [
      { module: "filesystem" },
      { module: "injection" },
      { module: "value_tamper" },
      { module: "overlay_hook" },
      { module: "godmode_runtime" },
      { module: "noclip_runtime" },
      { module: "aimbot_runtime" },
    ],
  },
  {
    id: "whistle_spoofing",
    label: "휘파람 위조",
    group: "gameplay",
    description: "휘파람 후킹 흔적과 비정상 RPC 호출을 검사",
    componentIds: ["whistle_spoofing"],
    eventAliases: [{ module: "whistle" }, { module: "whistle_rpc" }],
  },
  {
    id: "aimbot",
    label: "에임봇",
    group: "gameplay",
    description: "조준 입력과 표적 수렴 행동을 검사",
    componentIds: ["aimbot"],
    eventAliases: [{ module: "aimbot" }],
  },
  {
    id: "esp",
    label: "ESP",
    group: "gameplay",
    description: "외부 접근과 오버레이 정황의 상관관계를 검사",
    componentIds: ["esp"],
    eventAliases: [{ module: "esp" }],
  },
  {
    id: "godmode",
    label: "God Mode",
    group: "gameplay",
    description: "체력과 무적 상태 변조를 검사",
    componentIds: ["godmode"],
    eventAliases: [{ module: "godmode" }],
  },
  {
    id: "noclip",
    label: "Noclip",
    group: "gameplay",
    description: "충돌 해제와 벽 통과 이동을 검사",
    componentIds: ["noclip"],
    eventAliases: [{ module: "noclip" }],
  },
  {
    id: "autopaint",
    label: "Auto Paint",
    group: "gameplay",
    description: "자동 페인트 런타임과 행동 패턴을 검사",
    componentIds: ["autopaint"],
    eventAliases: [{ module: "autopaint" }],
  },
  {
    id: "hide_anywhere",
    label: "Hide Anywhere",
    group: "gameplay",
    description: "숨기 상호작용과 뷰포트 변조 정황을 검사",
    componentIds: ["hide_anywhere"],
    eventAliases: [{ module: "hide_anywhere" }],
  },
] as const satisfies readonly ProtectionModuleCatalogEntry[];

export const protectionModuleGroupLabels: Record<ProtectionModuleGroup, string> = {
  protection: "보호 계층",
  local_guard: "LocalGuard",
  gameplay: "게임 탐지",
  unmapped: "미등록 이벤트",
};

const normalized = (value: string): string => value.trim().toLocaleLowerCase("en-US");

function evidenceSubmodule(event: Pick<DashboardEvent, "evidence">): string | null {
  const value = event.evidence.submodule;
  return typeof value === "string" && value.trim() ? normalized(value) : null;
}

function unknownResolution(rawModule: string, submodule: string | null): ResolvedProtectionModule {
  const normalizedModule = normalized(rawModule) || "unknown";
  const readable = normalizedModule.replaceAll("_", " ");
  return {
    id: `unknown:${normalizedModule}`,
    label: `미등록 · ${readable}`,
    group: "unmapped",
    description: "보호 모듈 카탈로그에 연결되지 않은 Event module",
    known: false,
    rawModule: rawModule,
    submodule,
  };
}

function resolvedFromEntry(
  entry: ProtectionModuleCatalogEntry,
  rawModule: string,
  submodule: string | null,
): ResolvedProtectionModule {
  return {
    id: entry.id,
    label: entry.label,
    group: entry.group,
    description: entry.description,
    known: true,
    rawModule,
    submodule,
  };
}

export function findProtectionModule(id: string): ProtectionModuleCatalogEntry | null {
  const needle = normalized(id);
  return protectionModuleCatalog.find((entry) => (
    normalized(entry.id) === needle
    || entry.componentIds.some((componentId) => normalized(componentId) === needle)
  )) ?? null;
}

/** Resolve a Shared Event to the owning Launcher service. */
export function resolveEventModule(
  event: Pick<DashboardEvent, "module" | "evidence">,
): ResolvedProtectionModule {
  const rawModule = event.module;
  const moduleId = normalized(rawModule);
  const submodule = evidenceSubmodule(event);

  // Specific module+submodule mappings must win over a generic module alias.
  if (submodule !== null) {
    for (const entry of protectionModuleCatalog) {
      const matched = entry.eventAliases.some((alias) => (
        normalized(alias.module) === moduleId
        && "submodules" in alias
        && alias.submodules.some((value) => normalized(value) === submodule)
      ));
      if (matched) return resolvedFromEntry(entry, rawModule, submodule);
    }
  }

  for (const entry of protectionModuleCatalog) {
    const matched = entry.eventAliases.some((alias) => (
      normalized(alias.module) === moduleId && !("submodules" in alias)
    ));
    if (matched) return resolvedFromEntry(entry, rawModule, submodule);
  }

  // Keep legacy/direct service IDs visible without pretending a truly unknown
  // Event belongs to a known detector.
  const direct = protectionModuleCatalog.find((entry) => normalized(entry.id) === moduleId);
  return direct
    ? resolvedFromEntry(direct, rawModule, submodule)
    : unknownResolution(rawModule, submodule);
}

/**
 * Match a filter value against both raw Event names and logical service IDs.
 * Unknown options use the `unknown:<raw module>` value returned by
 * moduleFilterOptions().
 */
export function eventBelongsToModule(
  event: Pick<DashboardEvent, "module" | "evidence">,
  filterValue: string,
): boolean {
  if (filterValue === "ALL") return true;
  const resolved = resolveEventModule(event);
  const service = findProtectionModule(filterValue);
  if (service) return resolved.id === service.id;
  if (filterValue.startsWith("unknown:")) return resolved.id === filterValue;
  return normalized(event.module) === normalized(filterValue);
}

export function labelForModuleIdentifier(identifier: string): string | null {
  const direct = findProtectionModule(identifier);
  if (direct) return direct.label;

  const moduleId = normalized(identifier);
  for (const entry of protectionModuleCatalog) {
    if (entry.eventAliases.some((alias) => normalized(alias.module) === moduleId)) return entry.label;
  }
  return null;
}

/** Known service options plus any unmapped Event modules observed at runtime. */
export function moduleFilterOptions(
  events: readonly DashboardEvent[] = [],
): ModuleFilterOption[] {
  const options: ModuleFilterOption[] = protectionModuleCatalog.map((entry) => ({
    value: entry.id,
    label: entry.label,
    group: entry.group,
    known: true,
  }));
  const knownValues = new Set(options.map((option) => option.value));

  for (const event of events) {
    const resolved = resolveEventModule(event);
    if (resolved.known || knownValues.has(resolved.id)) continue;
    knownValues.add(resolved.id);
    options.push({
      value: resolved.id,
      label: resolved.label,
      group: resolved.group,
      known: false,
    });
  }
  return options;
}

const statePriority: readonly ComponentStatus[] = [
  "failed",
  "degraded",
  "stale",
  "unknown",
  "stopped",
  "starting",
  "running",
  "healthy",
];

function aggregateState(components: readonly ModuleStatus[]): ModuleRollupState {
  if (components.length === 0) return "not_reported";
  const states = new Set(components.map((component) => component.state));
  if (states.size === 1) return components[0]!.state;
  return statePriority.find((state) => states.has(state)) ?? "unknown";
}

interface MutableRollup extends ModuleRollup {
  subjects: Set<string>;
  latestSequence: number;
}

function catalogRollup(entry: ProtectionModuleCatalogEntry): MutableRollup {
  return {
    id: entry.id,
    label: entry.label,
    group: entry.group,
    description: entry.description,
    known: true,
    state: "not_reported",
    eventCount: 0,
    detectionCount: 0,
    operationalCount: 0,
    subjectCount: 0,
    latestEventAt: null,
    components: [],
    subjects: new Set(),
    latestSequence: -1,
  };
}

function unknownRollup(resolved: ResolvedProtectionModule): MutableRollup {
  return {
    id: resolved.id,
    label: resolved.label,
    group: resolved.group,
    description: resolved.description,
    known: false,
    state: "not_reported",
    eventCount: 0,
    detectionCount: 0,
    operationalCount: 0,
    subjectCount: 0,
    latestEventAt: null,
    components: [],
    subjects: new Set(),
    latestSequence: -1,
  };
}

/** Build a complete 13-service coverage view and retain unmapped inputs. */
export function buildModuleRollups(
  events: readonly DashboardEvent[],
  moduleStatuses: readonly ModuleStatus[],
): ModuleRollup[] {
  const rollups = new Map<string, MutableRollup>(
    protectionModuleCatalog.map((entry) => [entry.id, catalogRollup(entry)]),
  );

  for (const event of events) {
    const resolved = resolveEventModule(event);
    const rollup = rollups.get(resolved.id) ?? unknownRollup(resolved);
    rollups.set(resolved.id, rollup);
    rollup.eventCount += 1;
    if (event.event_kind === "detection") rollup.detectionCount += 1;
    if (event.event_kind === "operational") rollup.operationalCount += 1;
    rollup.subjects.add(`${event.session_id}::${event.player_id}`);
    // Sequence is the backend's stable receive order. timestamp_ms may have a
    // different origin in each session, so do not compare it across sessions.
    if (event.sequence > rollup.latestSequence) {
      rollup.latestSequence = event.sequence;
      rollup.latestEventAt = event.timestamp_ms;
    }
  }

  for (const component of moduleStatuses) {
    // Launcher is pipeline infrastructure, not one of the 13 protection
    // services represented by this catalog.
    if (normalized(component.id) === "launcher") continue;
    const entry = findProtectionModule(component.id);
    const id = entry?.id ?? `unknown:${normalized(component.id) || "unknown"}`;
    const resolved = entry
      ? resolvedFromEntry(entry, component.id, null)
      : unknownResolution(component.id, null);
    const rollup = rollups.get(id) ?? unknownRollup(resolved);
    rollups.set(id, rollup);
    rollup.components.push(component);
  }

  return [...rollups.values()].map(({ subjects, latestSequence: _latestSequence, ...rollup }) => ({
    ...rollup,
    state: aggregateState(rollup.components),
    subjectCount: subjects.size,
  }));
}
