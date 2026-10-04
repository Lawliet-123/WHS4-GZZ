import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DashboardApiError, fetchSnapshot, fetchSubjectStatus, loadDashboardBundle, loadEventDetail } from "./api";
import { ConnectionDialog } from "./components/ConnectionDialog";
import { EvidenceDrawer, redactSensitiveText } from "./components/EvidenceDrawer";
import { Icon, type IconName } from "./components/Icon";
import { StatusBadge } from "./components/StatusBadge";
import {
  connectionMeta,
  defaultFilters,
  filterAssessments,
  filterEvents,
  formatAge,
  formatDateTime,
  formatElapsed,
  humanizeModule,
  humanizeReason,
  subjectKey,
  verdictMeta,
} from "./domain";
import { demoEvents, demoOverview, demoSnapshots, demoStatuses } from "./mockData";
import { buildModuleRollups, moduleFilterOptions, protectionModuleGroupLabels } from "./moduleCatalog";
import type {
  Assessment,
  DashboardEvent,
  DashboardBundle,
  DashboardFilters,
  LiveConnectionInput,
  ModuleFilterOption,
  ModuleRollup,
  ProtectionModuleGroup,
  OverviewResponse,
  SnapshotResponse,
  SubjectStatusResponse,
  VerdictStatus,
} from "./types";

const EVENT_PAGE_SIZE = 8;
const TIMELINE_POINT_LIMIT = 160;
const dashboardSections: { id: string; label: string; icon: IconName }[] = [
  { id: "overview", label: "종합 현황", icon: "dashboard" },
  { id: "modules", label: "보호 모듈", icon: "shield" },
  { id: "sessions", label: "세션", icon: "timeline" },
  { id: "subjects", label: "플레이어 판정", icon: "user" },
  { id: "events", label: "이벤트", icon: "timeline" },
  { id: "systems", label: "시스템", icon: "server" },
];

const emptyConnection: LiveConnectionInput = { baseUrl: "/dashboard-api", token: "" };

const launcherStatePriority = [
  "failed",
  "unavailable",
  "stale",
  "degraded",
  "stopping",
  "starting",
  "stopped",
  "unknown",
  "healthy",
  "online",
] as const;

export function aggregateLauncherState(states: string[]): string {
  if (!states.length) return "unknown";
  if (states.every((state) => state === states[0])) return states[0]!;
  return launcherStatePriority.find((state) => states.includes(state)) ?? "unknown";
}

function loadStateWarning(bundle: DashboardBundle): string | null {
  const partial: string[] = [];
  if (bundle.load_state.overview.truncated) partial.push(`대상 ${bundle.load_state.overview.items_loaded}개`);
  if (bundle.load_state.events.truncated) partial.push(`이벤트 ${bundle.load_state.events.items_loaded}개`);
  return partial.length ? `${partial.join(", ")}까지만 불러왔습니다.` : null;
}

function Panel({ children, className = "", id }: { children: ReactNode; className?: string; id?: string }) {
  return <section id={id} data-dashboard-section={id ? true : undefined} className={`panel ${className}`.trim()}>{children}</section>;
}

function SectionHeader({ title, count, action }: { title: string; count?: string | number; action?: ReactNode }) {
  return (
    <div className="section-header">
      <div><h2>{title}</h2>{count !== undefined && <span>{count}</span>}</div>
      {action}
    </div>
  );
}

function EmptyState({ title, action }: { title: string; action?: ReactNode }) {
  return <div className="empty-state"><Icon name="database" size={22} /><strong>{title}</strong>{action}</div>;
}

interface PolicySignalView {
  state: string;
  emission: string;
  issues: string[];
}

function policySignals(snapshot: SnapshotResponse | null): Map<string, PolicySignalView> {
  const result = new Map<string, PolicySignalView>();
  const policy = snapshot?.policy;
  if (!policy || !Array.isArray(policy.modules)) return result;
  for (const entry of policy.modules) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;
    const state = record.state && typeof record.state === "object" ? record.state as Record<string, unknown> : {};
    const evaluation = record.evaluation && typeof record.evaluation === "object" ? record.evaluation as Record<string, unknown> : {};
    const signal = evaluation.signal && typeof evaluation.signal === "object" ? evaluation.signal as Record<string, unknown> : {};
    const view: PolicySignalView = {
      state: typeof signal.state === "string" ? signal.state : "UNKNOWN",
      emission: typeof signal.emission === "string" ? signal.emission : "unknown",
      issues: Array.isArray(signal.issues) ? signal.issues.filter((value): value is string => typeof value === "string") : [],
    };
    if (typeof state.event_id === "string") result.set(state.event_id, view);
    if (typeof state.module === "string") result.set(state.module, view);
  }
  return result;
}

function policyMeta(state: string): { label: string; tone: string } {
  const values: Record<string, { label: string; tone: string }> = {
    ACTIVE: { label: "활성 근거", tone: "danger" },
    INACTIVE: { label: "비활성", tone: "success" },
    ADVISORY: { label: "참고 근거", tone: "warning" },
    DEFERRED: { label: "평가 보류", tone: "warning" },
    UNRESOLVED: { label: "미해결", tone: "warning" },
    UNAVAILABLE: { label: "평가 불가", tone: "neutral" },
    RAW_FRACTION_ONLY: { label: "원본 비율", tone: "info" },
    POLICY_NOT_CALIBRATED: { label: "정책 미보정", tone: "warning" },
    UNKNOWN_MODULE: { label: "정책 없음", tone: "neutral" },
  };
  return values[state] ?? { label: state.replaceAll("_", " "), tone: "neutral" };
}

function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [activeId, setActiveId] = useState(() => window.location.hash.slice(1) || "overview");
  useEffect(() => {
    let frame = 0;
    const knownIds = new Set(dashboardSections.map((section) => section.id));
    const updateActiveSection = () => {
      frame = 0;
      const topbar = document.querySelector<HTMLElement>(".topbar");
      const activationLine = (topbar?.getBoundingClientRect().height || 76) + 12;
      const pageHeight = document.documentElement.scrollHeight;
      if (pageHeight > window.innerHeight + 2 && window.scrollY + window.innerHeight >= pageHeight - 2) {
        setActiveId(dashboardSections.at(-1)?.id ?? "overview");
        return;
      }
      let current = dashboardSections[0]?.id ?? "overview";
      for (const section of dashboardSections) {
        const element = document.getElementById(section.id);
        if (!element || element.getBoundingClientRect().top > activationLine) break;
        current = section.id;
      }
      setActiveId(current);
    };
    const scheduleUpdate = () => {
      if (frame === 0) frame = window.requestAnimationFrame(updateActiveSection);
    };
    const updateFromHash = () => {
      const hashId = window.location.hash.slice(1);
      if (knownIds.has(hashId)) setActiveId(hashId);
      scheduleUpdate();
    };
    if (knownIds.has(window.location.hash.slice(1))) {
      setActiveId(window.location.hash.slice(1));
    } else {
      scheduleUpdate();
    }
    window.addEventListener("scroll", scheduleUpdate, { passive: true });
    window.addEventListener("resize", scheduleUpdate);
    window.addEventListener("hashchange", updateFromHash);
    const sectionObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(scheduleUpdate);
    document.querySelectorAll<HTMLElement>("[data-dashboard-section]").forEach((section) => sectionObserver?.observe(section));
    return () => {
      window.removeEventListener("scroll", scheduleUpdate);
      window.removeEventListener("resize", scheduleUpdate);
      window.removeEventListener("hashchange", updateFromHash);
      sectionObserver?.disconnect();
      if (frame !== 0) window.cancelAnimationFrame(frame);
    };
  }, []);
  return (
    <>
      <button className={`sidebar-scrim ${open ? "visible" : ""}`} onClick={onClose} aria-label="메뉴 닫기" />
      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="brand"><span className="brand-mark"><Icon name="shield" size={22} /></span><div><strong>MECCHA</strong><span>Anti-Cheat</span></div></div>
        <nav aria-label="대시보드 메뉴">
          {dashboardSections.map((link) => <a key={link.id} href={`#${link.id}`} className={activeId === link.id ? "active" : ""} aria-current={activeId === link.id ? "location" : undefined} onClick={() => { setActiveId(link.id); onClose(); }}><Icon name={link.icon} /><span>{link.label}</span></a>)}
        </nav>
      </aside>
    </>
  );
}

function SummaryCard({ icon, label, value, tone }: { icon: IconName; label: string; value: number; tone: string }) {
  return <article className={`summary-card summary-${tone}`}><span className="summary-icon"><Icon name={icon} size={19} /></span><div><span>{label}</span><strong>{value.toLocaleString("ko-KR")}</strong></div></article>;
}

function ConnectionStrip({ overview, transportStale }: { overview: OverviewResponse; transportStale: boolean }) {
  const scopes: Record<string, string> = {
    local_detection_storage: "이벤트 저장소",
    local_scoring_read: "판정 조회",
    launcher_status: "실행 상태",
    returned_session_page: "세션 상태",
    all_returned_sessions: "전체 세션 상태",
  };
  return (
    <section className="connection-strip" aria-label="시스템 연결 상태">
      {Object.entries(overview.connection).map(([name, value]) => {
        const meta = transportStale ? { label: "갱신 실패", tone: "warning" } : connectionMeta(value.state);
        const scope = scopes[value.scope] ?? value.scope.replaceAll("_", " ");
        return <div className="connection-item" key={name} title={`${name} · ${scope}`}><span className={`connection-light tone-${meta.tone}`} /><div><span>{name}</span><strong>{meta.label}</strong><small>{scope}</small></div></div>;
      })}
      <div className="connection-scope">
        <Icon name="activity" size={15} />
        <span>인덱스 #{overview.index.through_sequence}</span>
        {overview.connection.Launcher.observed_pairs !== undefined && <span>{overview.connection.Launcher.connected_pairs ?? 0}/{overview.connection.Launcher.observed_pairs} 연결</span>}
        {overview.index.catching_up && <StatusBadge tone="warning">동기화 중</StatusBadge>}
      </div>
    </section>
  );
}

function DetailNotice({ loading, error }: { loading: boolean; error: string | null }) {
  if (error) return <div className="detail-notice detail-notice-error" role="alert"><Icon name="alert" size={14} /><span>{error}</span></div>;
  if (loading) return <div className="detail-notice" role="status"><span className="spinner" />최신 정보를 확인하는 중</div>;
  return null;
}

function FilterBar({ filters, overview, modules, submodules, onChange, onReset }: {
  filters: DashboardFilters;
  overview: OverviewResponse;
  modules: ModuleFilterOption[];
  submodules: string[];
  onChange: (filters: DashboardFilters) => void;
  onReset: () => void;
}) {
  const update = <K extends keyof DashboardFilters>(key: K, value: DashboardFilters[K]) => onChange({ ...filters, [key]: value });
  const visiblePlayers = filters.sessionId === "ALL"
    ? overview.players
    : overview.players.filter((player) => player.session_ids.includes(filters.sessionId));
  const updateSession = (sessionId: string) => {
    const selectedPlayerIsAvailable = sessionId === "ALL"
      || filters.playerId === "ALL"
      || overview.players.some((player) => player.id === filters.playerId && player.session_ids.includes(sessionId));
    onChange({
      ...filters,
      sessionId,
      playerId: selectedPlayerIsAvailable ? filters.playerId : "ALL",
    });
  };
  const changed = JSON.stringify(filters) !== JSON.stringify(defaultFilters);
  return (
    <div className="filter-bar">
      <label className="search-field"><Icon name="search" size={16} /><input aria-label="검색" value={filters.query} onChange={(event) => update("query", event.target.value)} placeholder="ID 또는 이벤트 근거 검색" /></label>
      <label className="select-field"><span>세션</span><select aria-label="세션" value={filters.sessionId} onChange={(event) => updateSession(event.target.value)}><option value="ALL">전체</option>{overview.sessions.map((session) => <option value={session.id} key={session.id}>{session.id}</option>)}</select></label>
      <label className="select-field"><span>플레이어</span><select aria-label="플레이어" value={filters.playerId} onChange={(event) => update("playerId", event.target.value)}><option value="ALL">전체</option>{visiblePlayers.map((player) => <option value={player.id} key={player.id}>{player.display_name}</option>)}</select></label>
      <label className="select-field"><span>보호 모듈</span><select aria-label="모듈" value={filters.module} onChange={(event) => update("module", event.target.value)}><option value="ALL">전체</option>{(["protection", "local_guard", "gameplay", "unmapped"] as ProtectionModuleGroup[]).map((group) => { const options = modules.filter((module) => module.group === group); return options.length ? <optgroup label={protectionModuleGroupLabels[group]} key={group}>{options.map((module) => <option value={module.value} key={module.value}>{module.label}</option>)}</optgroup> : null; })}</select></label>
      <label className="select-field"><span>세부 채널</span><select aria-label="세부 채널" value={filters.submodule} onChange={(event) => update("submodule", event.target.value)}><option value="ALL">전체</option>{submodules.map((submodule) => <option value={submodule} key={submodule}>{humanizeModule(submodule)}</option>)}</select></label>
      <label className="select-field"><span>이벤트 유형</span><select aria-label="이벤트 유형" value={filters.eventKind} onChange={(event) => update("eventKind", event.target.value as DashboardFilters["eventKind"])}><option value="ALL">전체</option><option value="detection">관측</option><option value="operational">운영</option></select></label>
      <label className="select-field"><span>판정</span><select value={filters.verdict} onChange={(event) => update("verdict", event.target.value as DashboardFilters["verdict"])}><option value="ALL">전체</option>{(Object.keys(verdictMeta) as VerdictStatus[]).map((status) => <option value={status} key={status}>{verdictMeta[status].label}</option>)}</select></label>
      {changed && <button className="filter-reset" type="button" onClick={onReset}><Icon name="close" size={14} />초기화</button>}
    </div>
  );
}

function SubjectList({ assessments, selected, onSelect }: { assessments: Assessment[]; selected: string; onSelect: (key: string) => void }) {
  const ranks: Record<VerdictStatus, number> = { SUSPICIOUS: 0, INCONCLUSIVE: 1, UNKNOWN: 2, NO_ACTIVE_EVIDENCE: 3 };
  const sorted = [...assessments].sort((a, b) => ranks[a.status] - ranks[b.status] || a.player_id.localeCompare(b.player_id));
  return (
    <div className="subject-list-wrap">
      <SectionHeader title="플레이어 판정" count={sorted.length} />
      {sorted.length ? <div className="subject-list">{sorted.map((item) => {
        const key = `${item.session_id}:${item.player_id}`;
        const meta = verdictMeta[item.status];
        return <button type="button" className={`subject-row ${selected === key ? "selected" : ""}`} key={key} onClick={() => onSelect(key)}><span className={`subject-state tone-${meta.tone}`}><Icon name={item.status === "SUSPICIOUS" ? "alert" : item.status === "NO_ACTIVE_EVIDENCE" ? "check" : "clock"} size={17} /></span><span className="subject-copy"><strong>{item.player_id}</strong><small>{item.session_id}</small></span><StatusBadge tone={meta.tone} dot={false}>{meta.short}</StatusBadge><Icon name="chevron" size={15} /></button>;
      })}</div> : <EmptyState title="조건에 맞는 대상 없음" />}
    </div>
  );
}

function VerdictView({ assessment, snapshot, loading, error }: {
  assessment: Assessment | null;
  snapshot: SnapshotResponse | null;
  loading: boolean;
  error: string | null;
}) {
  if (!assessment) return <div className="verdict-view"><EmptyState title="대상을 선택하세요" /></div>;
  const status = snapshot?.status ?? assessment.status;
  const meta = verdictMeta[status];
  const verdict = snapshot ? snapshot.final_verdict : assessment.final_verdict;
  const reasonCodes = snapshot ? snapshot.reason_codes : assessment.reason_codes;
  const assessmentAvailable = snapshot ? snapshot.assessment_available : assessment.assessment_available;
  const dataState = snapshot ? snapshot.data_state : assessment.data_state;
  const assessmentState = dataState === "not_connected"
    ? "Scoring 미연결"
    : dataState === "missing"
      ? "판정 데이터 없음"
      : assessmentAvailable
        ? (verdict?.assessment_complete ? "완료" : "미완료")
        : "판정 대기";
  const groups = verdict ? [
    { label: "활성", values: verdict.active_modules, tone: "danger" },
    { label: "참고", values: verdict.advisory_modules, tone: "warning" },
    { label: "미해결", values: verdict.unresolved_modules, tone: "info" },
    { label: "보류", values: verdict.deferred_modules, tone: "warning" },
    { label: "사용 불가", values: verdict.unavailable_modules, tone: "neutral" },
  ].filter((group) => group.values.length) : [];

  return (
    <div className="verdict-view">
      <DetailNotice loading={loading} error={error} />
      <div className="verdict-heading">
        <div><span>{assessment.session_id}</span><h2>{assessment.player_id}</h2></div>
        <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
      </div>
      <div className={`verdict-status tone-${meta.tone}`}><span className="verdict-icon"><Icon name={status === "SUSPICIOUS" ? "alert" : status === "NO_ACTIVE_EVIDENCE" ? "check" : "clock"} size={24} /></span><div><span>최종 판정</span><strong>{meta.label}</strong></div></div>

      <div className="verdict-metrics">
        <div><span>근거 단위</span><strong>{verdict?.evidence_unit_count ?? "—"}</strong></div>
        <div><span>활성 모듈</span><strong>{verdict?.active_module_count ?? "—"}</strong></div>
        <div><span>중복 보정</span><strong>{verdict?.overlap_adjustment_count ?? "—"}</strong></div>
        <div><span>평가 상태</span><strong className="metric-text">{assessmentState}</strong></div>
      </div>

      <div className="reason-block"><h3>판정 코드</h3>{reasonCodes.length ? <ul>{reasonCodes.map((reason) => <li key={reason}><Icon name="check" size={14} />{humanizeReason(reason)}</li>)}</ul> : <div className="empty-inline">판정 코드 없음</div>}</div>

      {groups.length > 0 && <div className="module-groups">{groups.map((group) => <div key={group.label}><span>{group.label}</span><div>{group.values.map((module) => <span className={`module-pill tone-${group.tone}`} key={module}>{humanizeModule(module)}</span>)}</div></div>)}</div>}
    </div>
  );
}

function ModuleStateList({ snapshot }: { snapshot: SnapshotResponse | null }) {
  const modules = snapshot?.modules ?? [];
  const signals = policySignals(snapshot);
  return (
    <div className="module-state-list">
      <SectionHeader title="최신 탐지 신호" count={modules.length} />
      {modules.length ? modules.map((item) => {
        const signal = signals.get(item.event_id) ?? signals.get(item.module);
        const meta = signal ? policyMeta(signal.state) : null;
        return <article className="module-state-card" key={item.event_id}><div><span className="module-glyph"><Icon name="activity" size={16} /></span><div><strong>{humanizeModule(item.module)}</strong><small>{formatElapsed(item.timestamp_ms)} · #{item.sequence}</small></div></div><span className="raw-chip">raw {item.raw_score}</span>{meta && <div className="module-policy-row"><StatusBadge tone={meta.tone}>{meta.label}</StatusBadge><span>{signal?.emission}</span></div>}{item.reasons.length > 0 && <p>{redactSensitiveText(item.reasons[0]!)}</p>}{signal?.issues[0] && <p className="policy-issue">{redactSensitiveText(signal.issues[0])}</p>}</article>;
      }) : <EmptyState title="탐지 신호 없음" />}
    </div>
  );
}

function Timeline({ events, onSelect }: { events: DashboardEvent[]; onSelect: (event: DashboardEvent) => void }) {
  const ordered = [...events].sort((a, b) => a.sequence - b.sequence);
  const timelinePoints = ordered.length <= TIMELINE_POINT_LIMIT
    ? ordered
    : Array.from({ length: TIMELINE_POINT_LIMIT }, (_, index) => ordered[Math.round((index * (ordered.length - 1)) / (TIMELINE_POINT_LIMIT - 1))]!);
  const latest = [...ordered].reverse().slice(0, 7);
  const firstSequence = ordered[0]?.sequence ?? 0;
  const lastSequence = ordered.at(-1)?.sequence ?? firstSequence;
  const sequenceSpan = Math.max(1, lastSequence - firstSequence);
  return (
    <Panel className="timeline-panel">
      <SectionHeader title="플레이어 타임라인" count={events.length} />
      {ordered.length > 0 && <div className="sequence-timeline" aria-label={`서버 수신 순서 타임라인 · ${events.length}개 중 ${timelinePoints.length}개 표식`}><div className="sequence-rail">{timelinePoints.map((event) => <button type="button" className={`sequence-point kind-${event.event_kind}`} style={{ left: `${((event.sequence - firstSequence) / sequenceSpan) * 100}%` }} key={event.id} onClick={() => onSelect(event)} title={`${humanizeModule(event.module)} · #${event.sequence}`} aria-label={`${humanizeModule(event.module)} ${event.event_kind === "operational" ? "운영" : "관측"} 이벤트 #${event.sequence}`} />)}</div><div className="sequence-labels"><span>#{firstSequence}</span><strong>서버 수신 순서</strong><span>#{lastSequence}</span></div></div>}
      <div className="timeline-subhead">최근 Event</div>
      {latest.length ? <ol className="timeline-list">{latest.map((event) => <li key={event.id}><button type="button" onClick={() => onSelect(event)}><time title={event.time_basis === "unknown" ? "시간 기준 미확인" : event.time_basis}>{formatElapsed(event.timestamp_ms)}</time><span className={`timeline-dot kind-${event.event_kind}`} /><span><strong>{humanizeModule(event.module)}</strong><small>{event.reasons[0] ? redactSensitiveText(event.reasons[0]) : (event.event_kind === "operational" ? "운영 상태" : "관측 신호")}</small></span><span className={`event-kind kind-${event.event_kind}`}>{event.event_kind === "operational" ? "운영" : "관측"}</span><Icon name="chevron" size={15} /></button></li>)}</ol> : <EmptyState title="표시할 이벤트 없음" />}
    </Panel>
  );
}

function detailSummary(details: Record<string, unknown>): string {
  const labels: Record<string, string> = {
    phase: "단계",
    launcher_status: "Launcher",
    last_code: "종료 코드",
    runs: "실행",
    restarts: "재시작",
  };
  return Object.entries(labels).flatMap(([key, label]) => {
    const value = details[key];
    return ["string", "number", "boolean"].includes(typeof value) ? [`${label} ${String(value)}`] : [];
  }).join(" · ");
}

function SubjectSystemStatus({ status, loading, error }: {
  status: SubjectStatusResponse | null;
  loading: boolean;
  error: string | null;
}) {
  if (!status) return <Panel className="health-panel"><SectionHeader title="실행 상태" /><DetailNotice loading={loading} error={error} />{!loading && !error && <EmptyState title="상태 데이터 없음" />}</Panel>;
  const launcher = connectionMeta(status.launcher.state);
  const components = status.sources.flatMap((source) => source.components.map((component) => ({ ...component, sourceId: source.client_id })));
  const statusReason = status.reason || status.launcher.reason;
  return (
    <Panel className="health-panel">
      <SectionHeader title="실행 상태" action={<StatusBadge tone={launcher.tone}>{launcher.label}</StatusBadge>} />
      <DetailNotice loading={loading} error={error} />
      <div className="health-summary"><div><span>Launcher</span><strong>{status.launcher.connected ? "연결" : "미연결"}</strong></div><div><span>상태 소스</span><strong>{status.sources.length}{status.has_more_sources ? "+" : ""}</strong></div></div>
      {statusReason && status.state !== "healthy" && <div className="health-note"><Icon name="info" size={14} /><span>{statusReason}</span></div>}
      <div className="component-list">{components.sort((a, b) => (a.state === "failed" || a.state === "degraded" || a.state === "stale" ? -1 : 0) - (b.state === "failed" || b.state === "degraded" || b.state === "stale" ? -1 : 0)).map((component) => { const meta = connectionMeta(component.state); const details = detailSummary(component.details); return <div className="component-row" key={`${component.sourceId}:${component.id}`}><span className={`connection-light tone-${meta.tone}`} /><div><strong>{humanizeModule(component.id)}</strong><small>{status.sources.length > 1 ? `${component.sourceId} · ` : ""}{formatAge(component.effective_age_ms)}{component.required ? " · 필수" : ""}{details ? ` · ${details}` : ""}</small></div><span>{meta.label}</span></div>; })}</div>
      {status.sources.map((source) => source.transport.configured && (source.transport.consecutive_failures > 0 || source.transport.last_error_type) ? <div className="transport-note" key={source.client_id}><strong>{source.client_id}</strong><span>전송 실패 {source.transport.consecutive_failures}회{source.transport.last_error_type ? ` · ${source.transport.last_error_type}` : ""}</span></div> : null)}
      {status.has_more_sources && <div className="health-more">일부 상태 소스만 표시됨</div>}
    </Panel>
  );
}

function ModuleOperations({ rollups, selected, onSelect }: { rollups: ModuleRollup[]; selected: string; onSelect: (moduleId: string) => void }) {
  const groups: ProtectionModuleGroup[] = ["protection", "local_guard", "gameplay", "unmapped"];
  const runtimeMeta = (state: ModuleRollup["state"]) => state === "not_reported"
    ? { label: "미보고", tone: "neutral" }
    : connectionMeta(state);
  return (
    <Panel id="modules" className="module-operations-panel">
      <SectionHeader title="보호 모듈" count={rollups.filter((item) => item.known).length} />
      <div className="module-group-grid">{groups.map((group) => {
        const items = rollups.filter((item) => item.group === group);
        if (!items.length) return null;
        return <section className={`module-group group-${group}`} key={group}><header><h3>{protectionModuleGroupLabels[group]}</h3><span>{items.length}</span></header><div>{items.map((item) => {
          const meta = runtimeMeta(item.state);
          return <button type="button" className={`module-overview-card ${selected === item.id ? "selected" : ""}`} key={item.id} onClick={() => onSelect(item.id)} aria-label={`${item.label} 필터 적용`}>
            <span className={`module-status-mark tone-${meta.tone}`}><Icon name={item.group === "protection" ? "shield" : item.group === "local_guard" ? "server" : "activity"} size={17} /></span>
            <span className="module-overview-copy"><strong>{item.label}</strong><small>{item.description}</small></span>
            <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
            <span className="module-overview-metrics"><span>상태 대상 {item.components.length}</span><span>Event {item.eventCount}</span><span>Event 대상 {item.subjectCount}</span></span>
          </button>;
        })}</div></section>;
      })}</div>
    </Panel>
  );
}

function SessionOverview({ overview, assessments, events, launcherStatuses, visibleSessionIds, onSelect }: {
  overview: OverviewResponse;
  assessments: Assessment[];
  events: DashboardEvent[];
  launcherStatuses: OverviewResponse["launcher_statuses"];
  visibleSessionIds: Set<string>;
  onSelect: (sessionId: string) => void;
}) {
  const launcherBySession = new Map<string, OverviewResponse["launcher_statuses"]>();
  for (const status of launcherStatuses) {
    const entries = launcherBySession.get(status.session_id) ?? [];
    entries.push(status);
    launcherBySession.set(status.session_id, entries);
  }
  const rank: Record<VerdictStatus, number> = { SUSPICIOUS: 0, INCONCLUSIVE: 1, UNKNOWN: 2, NO_ACTIVE_EVIDENCE: 3 };
  const sessionStatus = (sessionId: string, fallback: VerdictStatus): VerdictStatus => [...assessments]
    .filter((item) => item.session_id === sessionId)
    .sort((a, b) => rank[a.status] - rank[b.status])[0]?.status ?? fallback;
  const sorted = overview.sessions.filter((session) => visibleSessionIds.has(session.id)).sort((a, b) => {
    return rank[sessionStatus(a.id, a.status)] - rank[sessionStatus(b.id, b.status)] || a.id.localeCompare(b.id);
  });
  return (
    <Panel id="sessions" className="session-panel">
      <SectionHeader title="세션 현황" count={sorted.length} />
      {sorted.length ? <div className="session-table-wrap"><table className="session-table"><thead><tr><th>세션</th><th>플레이어</th><th>이벤트 채널</th><th>Launcher</th><th>플레이어 판정 요약</th><th>최대 관측 시각</th></tr></thead><tbody>{sorted.map((session) => {
        const sessionVerdict = sessionStatus(session.id, session.status);
        const verdict = verdictMeta[sessionVerdict];
        const scopedEvents = events.filter((item) => item.session_id === session.id);
        const scopedPlayers = new Set([
          ...assessments.filter((item) => item.session_id === session.id).map((item) => item.player_id),
          ...scopedEvents.map((item) => item.player_id),
        ]);
        const scopedChannels = new Set(scopedEvents.map((item) => item.module));
        const scopedMaximumTimestamp = scopedEvents.length ? Math.max(...scopedEvents.map((item) => item.timestamp_ms)) : null;
        const launchers = launcherBySession.get(session.id) ?? [];
        const connected = launchers.filter((item) => item.connected).length;
        const launcherState = aggregateLauncherState(launchers.map((item) => item.state));
        const launcherMeta = connectionMeta(launcherState);
        const launcherLabel = launchers.length === 0
          ? launcherMeta.label
          : ["healthy", "online"].includes(launcherState) && connected === launchers.length
            ? `${connected}/${launchers.length} 연결`
            : `${launcherMeta.label} · ${connected}/${launchers.length}`;
        return <tr key={session.id} onClick={() => onSelect(session.id)}><td><button className="session-link" type="button" onClick={() => onSelect(session.id)}>{session.id}</button></td><td>{scopedPlayers.size}</td><td>{scopedChannels.size}</td><td><StatusBadge tone={launcherMeta.tone}>{launcherLabel}</StatusBadge></td><td><StatusBadge tone={verdict.tone}>{verdict.short}</StatusBadge></td><td>{scopedMaximumTimestamp === null ? "—" : formatElapsed(scopedMaximumTimestamp)}</td></tr>;
      })}</tbody></table></div> : <EmptyState title="세션 데이터 없음" />}
    </Panel>
  );
}

function SystemOverview({ overview, launcherStatuses, operationalEventCount }: {
  overview: OverviewResponse;
  launcherStatuses: OverviewResponse["launcher_statuses"];
  operationalEventCount: number;
}) {
  const launcherCounts = launcherStatuses.reduce<Record<string, number>>((counts, item) => {
    counts[item.state] = (counts[item.state] ?? 0) + 1;
    return counts;
  }, {});
  const capabilityLabels: Array<[keyof OverviewResponse["capabilities"], string]> = [
    ["final_assessment", "최종 판정"],
    ["launcher_heartbeat", "Launcher Heartbeat"],
    ["heartbeat_query", "Heartbeat 조회"],
    ["evidence_images", "증거 이미지"],
  ];
  return (
    <Panel id="systems" className="system-overview-panel">
      <SectionHeader title="통합 상태" count={launcherStatuses.length} />
      <div className="system-overview-grid">
        <div className="system-block">
          <h3>Launcher 연결</h3>
          <div className="system-facts"><div><span>관측 대상</span><strong>{launcherStatuses.length}</strong></div><div><span>연결 대상</span><strong>{launcherStatuses.filter((item) => item.connected).length}</strong></div><div><span>Heartbeat</span><strong>{overview.capabilities.launcher_heartbeat ? "지원" : "미지원"}</strong></div></div>
          {Object.keys(launcherCounts).length > 0 && <div className="state-counts">{Object.entries(launcherCounts).map(([state, count]) => { const meta = connectionMeta(state); return <span className={`tone-${meta.tone}`} key={state}>{meta.label} {count}</span>; })}</div>}
        </div>
        <div className="system-block">
          <h3>Backend 기능</h3>
          <div className="capability-list">{capabilityLabels.map(([key, label]) => <div key={key}><span>{label}</span><StatusBadge tone={overview.capabilities[key] ? "success" : "neutral"}>{overview.capabilities[key] ? "사용 가능" : "미지원"}</StatusBadge></div>)}</div>
        </div>
        <div className="system-block">
          <h3>Event 인덱스</h3>
          <div className="system-facts"><div><span>처리 순번</span><strong>#{overview.index.through_sequence}</strong></div><div><span>상태</span><strong>{overview.index.catching_up ? "동기화 중" : "최신"}</strong></div><div><span>운영 Event</span><strong>{operationalEventCount}</strong></div></div>
        </div>
      </div>
      <div className="fleet-status-list"><h3>클라이언트 상태</h3>{launcherStatuses.length ? launcherStatuses.map((item) => {
        const meta = connectionMeta(item.state);
        const source = item.source;
        return <div className="fleet-status-row" key={`${item.session_id}:${item.player_id}`}><span className={`connection-light tone-${meta.tone}`} /><div><strong>{item.player_id}</strong><small>{item.session_id}</small></div><StatusBadge tone={meta.tone}>{meta.label}</StatusBadge><div><strong>{source?.client_id ?? "소스 없음"}</strong><small>{source ? `${formatDateTime(source.received_at_utc)} · seq ${source.sequence}` : item.reason || "Heartbeat 없음"}</small></div><div><strong>{source?.transport.configured ? "중앙 전송" : "로컬 상태"}</strong><small>{source?.transport.configured ? `실패 ${source.transport.consecutive_failures}회 · 마지막 #${source.transport.last_success_sequence ?? "—"}` : "전송 미설정"}</small></div></div>;
      }) : <EmptyState title="Launcher 상태 없음" />}</div>
    </Panel>
  );
}

function EventsTable({ events, page, onPage, onSelect }: { events: DashboardEvent[]; page: number; onPage: (page: number) => void; onSelect: (event: DashboardEvent) => void }) {
  const pageCount = Math.max(1, Math.ceil(events.length / EVENT_PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const visible = events.slice((safePage - 1) * EVENT_PAGE_SIZE, safePage * EVENT_PAGE_SIZE);
  return (
    <Panel id="events" className="events-panel">
        <SectionHeader title="전체 이벤트" count={events.length} />
      {visible.length ? <><div className="table-wrap"><table><thead><tr><th>시간</th><th>유형</th><th>대상</th><th>채널</th><th>근거</th><th>Raw</th><th /></tr></thead><tbody>{visible.map((event) => <tr key={event.id} onClick={() => onSelect(event)}><td><span className="time-cell" title={event.time_basis === "unknown" ? "시간 기준 미확인" : event.time_basis}>{formatElapsed(event.timestamp_ms)}</span></td><td><span className={`event-kind kind-${event.event_kind}`}>{event.event_kind === "operational" ? "운영" : "관측"}</span></td><td><strong>{event.player_id}</strong><small>{event.session_id}</small></td><td><span className="module-pill">{humanizeModule(event.module)}</span>{typeof event.evidence.submodule === "string" && <small>{humanizeModule(event.evidence.submodule)}</small>}</td><td><strong>{event.reasons[0] ? redactSensitiveText(event.reasons[0]) : (event.event_kind === "operational" ? "운영 상태" : "—")}</strong><small>#{event.sequence}</small></td><td><span className="raw-chip">{event.raw_score}</span></td><td><button type="button" className="row-open" aria-label={`${event.player_id} ${humanizeModule(event.module)} 이벤트 #${event.sequence} 상세 보기`}><Icon name="chevron" size={15} /></button></td></tr>)}</tbody></table></div><div className="pagination"><span>{(safePage - 1) * EVENT_PAGE_SIZE + 1}–{Math.min(safePage * EVENT_PAGE_SIZE, events.length)} / {events.length}</span><div><button type="button" onClick={() => onPage(safePage - 1)} disabled={safePage === 1}>이전</button><span>{safePage} / {pageCount}</span><button type="button" onClick={() => onPage(safePage + 1)} disabled={safePage === pageCount}>다음</button></div></div></> : <EmptyState title="조건에 맞는 이벤트 없음" />}
    </Panel>
  );
}

export default function App() {
  const [overview, setOverview] = useState<OverviewResponse>(demoOverview);
  const [events, setEvents] = useState<DashboardEvent[]>(demoEvents.items);
  const [mode, setMode] = useState<"demo" | "live">("demo");
  const [connection, setConnection] = useState<LiveConnectionInput>(emptyConnection);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [filters, setFilters] = useState<DashboardFilters>(defaultFilters);
  const [selectedKey, setSelectedKey] = useState(demoOverview.assessments[0]?.id ?? "");
  const [snapshot, setSnapshot] = useState<SnapshotResponse | null>(null);
  const [subjectStatus, setSubjectStatus] = useState<SubjectStatusResponse | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<DashboardEvent | null>(null);
  const [eventLoading, setEventLoading] = useState(false);
  const [eventError, setEventError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [statusLoading, setStatusLoading] = useState(false);
  const [transportStale, setTransportStale] = useState(false);
  const [loadWarning, setLoadWarning] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [lastQueryAt, setLastQueryAt] = useState(new Date());
  const detailRequestRef = useRef(0);
  const refreshInFlightRef = useRef(false);
  const connectInFlightRef = useRef(false);
  const refreshControllerRef = useRef<AbortController | null>(null);
  const connectControllerRef = useRef<AbortController | null>(null);
  const eventControllerRef = useRef<AbortController | null>(null);
  const eventRequestRef = useRef(0);

  const modules = useMemo(() => moduleFilterOptions(events), [events]);
  const submodules = useMemo(() => [...new Set(events.map((event) => typeof event.evidence.submodule === "string" ? event.evidence.submodule : "").filter(Boolean))].sort(), [events]);
  const filteredEvents = useMemo(() => filterEvents(events, overview.assessments, filters), [events, filters, overview.assessments]);
  const filteredAssessments = useMemo(() => filterAssessments(overview.assessments, events, filters), [events, filters, overview.assessments]);
  const filtersActive = JSON.stringify(filters) !== JSON.stringify(defaultFilters);
  const evidenceFiltersActive = filters.module !== "ALL"
    || filters.submodule !== "ALL"
    || filters.eventKind !== "ALL"
    || filters.verdict !== "ALL"
    || filters.query.trim().length > 0;
  const subjectMatchesSelection = useCallback((sessionId: string, playerId: string) => (
    (filters.sessionId === "ALL" || filters.sessionId === sessionId)
    && (filters.playerId === "ALL" || filters.playerId === playerId)
  ), [filters.playerId, filters.sessionId]);
  const visibleSubjectKeys = useMemo(() => new Set([
    ...filteredAssessments.map((item) => subjectKey(item.session_id, item.player_id)),
    ...filteredEvents.map((item) => subjectKey(item.session_id, item.player_id)),
  ]), [filteredAssessments, filteredEvents]);
  const moduleScopeEvents = useMemo(() => filterEvents(events, overview.assessments, { ...filters, module: "ALL" }), [events, filters, overview.assessments]);
  const moduleScopeAssessments = useMemo(() => filterAssessments(overview.assessments, events, { ...filters, module: "ALL" }), [events, filters, overview.assessments]);
  const moduleScopeSubjectKeys = useMemo(() => new Set([
    ...moduleScopeAssessments.map((item) => subjectKey(item.session_id, item.player_id)),
    ...moduleScopeEvents.map((item) => subjectKey(item.session_id, item.player_id)),
  ]), [moduleScopeAssessments, moduleScopeEvents]);
  const moduleScopeEvidenceFiltersActive = filters.submodule !== "ALL"
    || filters.eventKind !== "ALL"
    || filters.verdict !== "ALL"
    || filters.query.trim().length > 0;
  const moduleScopeStatuses = useMemo(() => overview.module_statuses.filter((item) => {
    if (!filtersActive) return true;
    if (!moduleScopeEvidenceFiltersActive) return subjectMatchesSelection(item.session_id, item.player_id);
    return moduleScopeSubjectKeys.has(subjectKey(item.session_id, item.player_id));
  }), [filtersActive, moduleScopeEvidenceFiltersActive, moduleScopeSubjectKeys, overview.module_statuses, subjectMatchesSelection]);
  const moduleRollups = useMemo(() => buildModuleRollups(moduleScopeEvents, moduleScopeStatuses), [moduleScopeEvents, moduleScopeStatuses]);
  const filteredLauncherStatuses = useMemo(() => overview.launcher_statuses.filter((item) => {
    if (!filtersActive) return true;
    if (!evidenceFiltersActive) return subjectMatchesSelection(item.session_id, item.player_id);
    return visibleSubjectKeys.has(subjectKey(item.session_id, item.player_id));
  }), [evidenceFiltersActive, filtersActive, overview.launcher_statuses, subjectMatchesSelection, visibleSubjectKeys]);
  const visibleSessionIds = useMemo(() => {
    if (!filtersActive) return new Set(overview.sessions.map((session) => session.id));
    return new Set([
      ...filteredAssessments.map((item) => item.session_id),
      ...filteredEvents.map((item) => item.session_id),
      ...filteredLauncherStatuses.map((item) => item.session_id),
    ]);
  }, [filteredAssessments, filteredEvents, filteredLauncherStatuses, filtersActive, overview.sessions]);
  const visiblePlayerIds = useMemo(() => new Set([
    ...filteredAssessments.map((item) => item.player_id),
    ...filteredEvents.map((item) => item.player_id),
    ...filteredLauncherStatuses.map((item) => item.player_id),
  ]), [filteredAssessments, filteredEvents, filteredLauncherStatuses]);
  const selectedAssessment = filteredAssessments.find((item) => item.id === selectedKey) ?? filteredAssessments[0] ?? null;
  const subjectEvents = selectedAssessment ? filteredEvents.filter((event) => event.session_id === selectedAssessment.session_id && event.player_id === selectedAssessment.player_id) : [];
  const selectedSubjectKey = selectedAssessment ? subjectKey(selectedAssessment.session_id, selectedAssessment.player_id) : "";
  const visibleSnapshot = snapshot && subjectKey(snapshot.session_id, snapshot.player_id) === selectedSubjectKey ? snapshot : null;
  const visibleSubjectStatus = subjectStatus && subjectKey(subjectStatus.session_id, subjectStatus.player_id) === selectedSubjectKey ? subjectStatus : null;

  useEffect(() => {
    const assessment = selectedAssessment;
    const requestId = ++detailRequestRef.current;
    const controller = new AbortController();
    setSnapshotError(null);
    setStatusError(null);
    if (!assessment) {
      setSnapshot(null);
      setSubjectStatus(null);
      setSnapshotLoading(false);
      setStatusLoading(false);
      return () => controller.abort();
    }

    const expectedKey = subjectKey(assessment.session_id, assessment.player_id);
    setSnapshot((current) => current && subjectKey(current.session_id, current.player_id) === expectedKey ? current : null);
    setSubjectStatus((current) => current && subjectKey(current.session_id, current.player_id) === expectedKey ? current : null);
    if (mode === "demo") {
      setSnapshot(demoSnapshots[expectedKey] ?? null);
      setSubjectStatus(demoStatuses[expectedKey] ?? null);
      setSnapshotLoading(false);
      setStatusLoading(false);
      return () => controller.abort();
    }

    setSnapshotLoading(true);
    setStatusLoading(true);
    void fetchSnapshot(connection, assessment.session_id, assessment.player_id, controller.signal)
      .then((detail) => {
        if (controller.signal.aborted || requestId !== detailRequestRef.current) return;
        if (subjectKey(detail.session_id, detail.player_id) !== expectedKey) throw new DashboardApiError("선택한 대상과 다른 판정 응답이 도착했습니다.");
        setSnapshot(detail);
      })
      .catch((caught) => {
        if (controller.signal.aborted || requestId !== detailRequestRef.current) return;
        setSnapshotError(caught instanceof DashboardApiError ? caught.message : "대상 판정 정보를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (!controller.signal.aborted && requestId === detailRequestRef.current) setSnapshotLoading(false);
      });

    void fetchSubjectStatus(connection, assessment.session_id, assessment.player_id, controller.signal)
      .then((detail) => {
        if (controller.signal.aborted || requestId !== detailRequestRef.current) return;
        if (subjectKey(detail.session_id, detail.player_id) !== expectedKey) throw new DashboardApiError("선택한 대상과 다른 실행 상태 응답이 도착했습니다.");
        setSubjectStatus(detail);
      })
      .catch((caught) => {
        if (controller.signal.aborted || requestId !== detailRequestRef.current) return;
        setStatusError(caught instanceof DashboardApiError ? caught.message : "대상 실행 상태를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (!controller.signal.aborted && requestId === detailRequestRef.current) setStatusLoading(false);
      });

    return () => controller.abort();
  }, [connection, mode, overview.generated_at_utc, selectedAssessment?.id]);

  useEffect(() => {
    setPage(1);
    const first = filteredAssessments[0];
    if (!first) setSelectedKey("");
    else if (!filteredAssessments.some((item) => item.id === selectedKey)) setSelectedKey(first.id);
  }, [filteredAssessments, selectedKey]);

  const refresh = useCallback(async () => {
    if (mode === "demo") { setLastQueryAt(new Date()); return; }
    if (refreshInFlightRef.current || connectInFlightRef.current) return;
    refreshInFlightRef.current = true;
    const controller = new AbortController();
    refreshControllerRef.current = controller;
    setRefreshing(true); setError(null);
    try {
      const afterSequence = events.reduce((maximum, event) => Math.max(maximum, event.sequence), 0);
      let resetEventFeed = false;
      let bundle: DashboardBundle;
      try {
        bundle = await loadDashboardBundle(connection, undefined, afterSequence, controller.signal);
      } catch (caught) {
        if (!(caught instanceof DashboardApiError) || caught.status !== 422 || afterSequence === 0) throw caught;
        bundle = await loadDashboardBundle(connection, undefined, 0, controller.signal);
        resetEventFeed = true;
      }
      if (controller.signal.aborted) return;
      setOverview(bundle.overview);
      setLoadWarning(loadStateWarning(bundle));
      setEvents((current) => {
        if (resetEventFeed) return bundle.events.items;
        const merged = new Map(current.map((event) => [event.id, event]));
        for (const event of bundle.events.items) merged.set(event.id, event);
        return [...merged.values()];
      });
      setTransportStale(false);
      setLastQueryAt(new Date());
    } catch (caught) {
      if (controller.signal.aborted) return;
      setTransportStale(true);
      setError(caught instanceof DashboardApiError ? caught.message : "데이터를 새로고치지 못했습니다.");
    } finally {
      if (refreshControllerRef.current === controller) {
        refreshControllerRef.current = null;
        refreshInFlightRef.current = false;
        setRefreshing(false);
      }
    }
  }, [connection, events, mode]);

  useEffect(() => {
    if (mode !== "live" || !autoRefresh) return;
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, mode, refresh]);

  const connect = async (input: LiveConnectionInput) => {
    refreshControllerRef.current?.abort();
    refreshControllerRef.current = null;
    refreshInFlightRef.current = false;
    setRefreshing(false);
    eventControllerRef.current?.abort();
    eventControllerRef.current = null;
    eventRequestRef.current += 1;
    setEventLoading(false);
    setEventError(null);
    setSelectedEvent(null);
    connectControllerRef.current?.abort();
    const controller = new AbortController();
    connectControllerRef.current = controller;
    connectInFlightRef.current = true;
    setLoading(true); setError(null); setConnectionError(null);
    try {
      const bundle = await loadDashboardBundle(input, undefined, 0, controller.signal);
      if (controller.signal.aborted) return;
      setConnection(input); setOverview(bundle.overview); setEvents(bundle.events.items); setMode("live"); setFilters(defaultFilters); setLoadWarning(loadStateWarning(bundle)); setTransportStale(false);
      setSelectedKey(bundle.overview.assessments[0]?.id ?? ""); setConnectionOpen(false); setLastQueryAt(new Date());
    } catch (caught) {
      if (controller.signal.aborted) return;
      setConnectionError(caught instanceof DashboardApiError ? caught.message : "중앙 서버에 연결하지 못했습니다.");
    } finally {
      if (connectControllerRef.current === controller) {
        connectControllerRef.current = null;
        connectInFlightRef.current = false;
        setLoading(false);
      }
    }
  };

  const useDemo = () => {
    connectControllerRef.current?.abort();
    connectControllerRef.current = null;
    connectInFlightRef.current = false;
    setLoading(false);
    refreshControllerRef.current?.abort();
    refreshControllerRef.current = null;
    refreshInFlightRef.current = false;
    setRefreshing(false);
    eventControllerRef.current?.abort();
    eventControllerRef.current = null;
    eventRequestRef.current += 1;
    setEventLoading(false);
    setEventError(null);
    setSelectedEvent(null);
    setOverview(demoOverview); setEvents(demoEvents.items); setMode("demo"); setFilters(defaultFilters);
    setSelectedKey(demoOverview.assessments[0]?.id ?? ""); setError(null); setConnectionError(null); setLoadWarning(null); setTransportStale(false); setLastQueryAt(new Date());
  };

  const openEvent = async (event: DashboardEvent) => {
    eventControllerRef.current?.abort();
    const controller = new AbortController();
    eventControllerRef.current = controller;
    const requestId = ++eventRequestRef.current;
    setSelectedEvent(event);
    setEventError(null);
    if (mode !== "live") return;
    setEventLoading(true);
    try {
      const detail = await loadEventDetail(connection, event.id, controller.signal);
      if (!controller.signal.aborted && requestId === eventRequestRef.current && detail.id === event.id) setSelectedEvent(detail);
    } catch (caught) {
      if (!controller.signal.aborted && requestId === eventRequestRef.current) setEventError(caught instanceof DashboardApiError ? caught.message : "이벤트 상세를 불러오지 못했습니다.");
    } finally {
      if (eventControllerRef.current === controller) {
        eventControllerRef.current = null;
        setEventLoading(false);
      }
    }
  };

  useEffect(() => () => {
    connectControllerRef.current?.abort();
    refreshControllerRef.current?.abort();
    eventControllerRef.current?.abort();
  }, []);

  const suspiciousCount = filteredAssessments.filter((item) => item.status === "SUSPICIOUS").length;
  const inconclusiveCount = filteredAssessments.filter((item) => item.status === "INCONCLUSIVE").length;
  const operationalEventCount = filteredEvents.filter((item) => item.event_kind === "operational").length;
  const unhealthyModuleCount = moduleRollups.filter((item) => item.known && ["degraded", "failed", "stale", "stopped"].includes(item.state)).length;

  return (
    <div className="app-shell">
      <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} />
      <main className="main-shell">
        <header className="topbar">
          <div className="topbar-title"><button className="menu-button" type="button" onClick={() => setMenuOpen(true)} aria-label="메뉴 열기"><Icon name="menu" /></button><div><strong>통합 관제</strong></div></div>
          <div className="topbar-actions">
            <StatusBadge tone={mode === "demo" ? "info" : transportStale ? "warning" : "success"}>{mode === "demo" ? "DEMO" : transportStale ? "LIVE · 지연" : "LIVE"}</StatusBadge>
            <time className="last-query" dateTime={lastQueryAt.toISOString()} aria-label={`마지막 갱신 ${lastQueryAt.toLocaleString("ko-KR")}`}>{new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(lastQueryAt)}</time>
            {mode === "live" && <label className="auto-toggle"><input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} /><span aria-hidden="true" /><b>5초 갱신</b></label>}
            <button className="icon-button" type="button" onClick={() => void refresh()} disabled={refreshing} aria-label="새로고침"><Icon name="refresh" className={refreshing ? "spin" : ""} /></button>
            {mode === "live" && <button className="button button-quiet desktop-action" type="button" onClick={useDemo}>시연 보기</button>}
            <button className="button button-primary" type="button" onClick={() => { setConnectionError(null); setConnectionOpen(true); }}><Icon name="server" />연결</button>
          </div>
        </header>

        <div className="content-shell">
          {error && <div className="error-banner" role="alert"><Icon name="alert" /><span>{error}</span><button type="button" onClick={() => setError(null)} aria-label="오류 닫기"><Icon name="close" size={16} /></button></div>}
          {loadWarning && <div className="error-banner warning-banner" role="status"><Icon name="info" /><span>{loadWarning}</span><button type="button" onClick={() => setLoadWarning(null)} aria-label="안내 닫기"><Icon name="close" size={16} /></button></div>}
          <section id="overview" data-dashboard-section className="overview-section" aria-label="종합 현황">
            <ConnectionStrip overview={overview} transportStale={transportStale} />
            <FilterBar filters={filters} overview={overview} modules={modules} submodules={submodules} onChange={setFilters} onReset={() => setFilters(defaultFilters)} />
            <div className="summary-grid" aria-label="요약">
              <SummaryCard icon="timeline" label="조회 세션" value={visibleSessionIds.size} tone="blue" />
              <SummaryCard icon="user" label="조회 플레이어" value={visiblePlayerIds.size} tone="violet" />
              <SummaryCard icon="database" label="기록 Event" value={filteredEvents.length} tone="navy" />
              <SummaryCard icon="activity" label="운영 Event" value={operationalEventCount} tone="green" />
              <SummaryCard icon="alert" label="검토 대상" value={suspiciousCount + inconclusiveCount} tone="red" />
              <SummaryCard icon="clock" label="모듈 이상" value={unhealthyModuleCount} tone="amber" />
            </div>
          </section>

          <ModuleOperations rollups={moduleRollups} selected={filters.module} onSelect={(moduleId) => { setFilters({ ...filters, module: filters.module === moduleId ? "ALL" : moduleId }); document.getElementById("events")?.scrollIntoView({ behavior: "smooth" }); }} />

          <SessionOverview overview={overview} assessments={filteredAssessments} events={filteredEvents} launcherStatuses={filteredLauncherStatuses} visibleSessionIds={visibleSessionIds} onSelect={(sessionId) => { setFilters({ ...filters, sessionId, playerId: "ALL" }); document.getElementById("subjects")?.scrollIntoView({ behavior: "smooth" }); }} />

          <Panel id="subjects" className="workspace-panel">
            <div className="workspace-grid"><SubjectList assessments={filteredAssessments} selected={selectedAssessment?.id ?? ""} onSelect={setSelectedKey} /><VerdictView assessment={selectedAssessment} snapshot={visibleSnapshot} loading={snapshotLoading} error={snapshotError} /><ModuleStateList snapshot={visibleSnapshot} /></div>
          </Panel>

          <div className="insight-grid"><Timeline events={subjectEvents} onSelect={(event) => void openEvent(event)} /><SubjectSystemStatus status={visibleSubjectStatus} loading={statusLoading} error={statusError} /></div>
          <EventsTable events={filteredEvents} page={page} onPage={setPage} onSelect={(event) => void openEvent(event)} />
          <SystemOverview overview={overview} launcherStatuses={filteredLauncherStatuses} operationalEventCount={operationalEventCount} />
        </div>
      </main>

      {(loading || refreshing) && <div className="refresh-indicator" aria-live="polite"><span className="spinner" />{loading ? "연결 중" : "새로고침 중"}</div>}
      <EvidenceDrawer event={selectedEvent} loading={eventLoading} error={eventError ?? undefined} onRetry={selectedEvent ? () => void openEvent(selectedEvent) : undefined} evidenceImagesAvailable={overview.capabilities.evidence_images} onClose={() => { eventControllerRef.current?.abort(); eventRequestRef.current += 1; setEventLoading(false); setEventError(null); setSelectedEvent(null); }} />
      <ConnectionDialog open={connectionOpen} loading={loading} error={connectionError ?? undefined} initial={connection} onClose={() => setConnectionOpen(false)} onConnect={(input) => void connect(input)} />
    </div>
  );
}
