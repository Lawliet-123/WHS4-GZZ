import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DashboardApiError, loadDashboardBundle, loadEventDetail, loadSubjectDetail } from "./api";
import { ConnectionDialog } from "./components/ConnectionDialog";
import { EvidenceDrawer } from "./components/EvidenceDrawer";
import { Icon, type IconName } from "./components/Icon";
import { StatusBadge } from "./components/StatusBadge";
import {
  connectionMeta,
  defaultFilters,
  filterAssessments,
  filterEvents,
  formatAge,
  formatElapsed,
  humanizeModule,
  humanizeReason,
  subjectKey,
  verdictMeta,
} from "./domain";
import { demoEvents, demoOverview, demoSnapshots, demoStatuses } from "./mockData";
import type {
  Assessment,
  DashboardEvent,
  DashboardBundle,
  DashboardFilters,
  LiveConnectionInput,
  OverviewResponse,
  SnapshotResponse,
  SubjectStatusResponse,
  VerdictStatus,
} from "./types";

const EVENT_PAGE_SIZE = 8;

const emptyConnection: LiveConnectionInput = { baseUrl: "/dashboard-api", token: "" };

function loadStateWarning(bundle: DashboardBundle): string | null {
  const partial: string[] = [];
  if (bundle.load_state.overview.truncated) partial.push(`대상 ${bundle.load_state.overview.items_loaded}개`);
  if (bundle.load_state.events.truncated) partial.push(`이벤트 ${bundle.load_state.events.items_loaded}개`);
  return partial.length ? `${partial.join(", ")}까지만 불러왔습니다.` : null;
}

function Panel({ children, className = "", id }: { children: ReactNode; className?: string; id?: string }) {
  return <section id={id} className={`panel ${className}`.trim()}>{children}</section>;
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

function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [activeId, setActiveId] = useState(() => window.location.hash.slice(1) || "overview");
  const links: { id: string; label: string; icon: IconName }[] = [
    { id: "overview", label: "현황", icon: "dashboard" },
    { id: "subjects", label: "탐지 대상", icon: "user" },
    { id: "events", label: "이벤트", icon: "timeline" },
    { id: "systems", label: "시스템", icon: "server" },
  ];
  return (
    <>
      <button className={`sidebar-scrim ${open ? "visible" : ""}`} onClick={onClose} aria-label="메뉴 닫기" />
      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="brand"><span className="brand-mark"><Icon name="shield" size={22} /></span><div><strong>MECCHA</strong><span>Anti-Cheat</span></div></div>
        <nav aria-label="대시보드 메뉴">
          {links.map((link) => <a key={link.id} href={`#${link.id}`} className={activeId === link.id ? "active" : ""} onClick={() => { setActiveId(link.id); onClose(); }}><Icon name={link.icon} /><span>{link.label}</span></a>)}
        </nav>
      </aside>
    </>
  );
}

function SummaryCard({ icon, label, value, tone }: { icon: IconName; label: string; value: number; tone: string }) {
  return <article className={`summary-card summary-${tone}`}><span className="summary-icon"><Icon name={icon} size={19} /></span><div><span>{label}</span><strong>{value.toLocaleString("ko-KR")}</strong></div></article>;
}

function ConnectionStrip({ overview }: { overview: OverviewResponse }) {
  const scopes: Record<string, string> = {
    local_detection_storage: "이벤트 저장소",
    local_scoring_read: "판정 조회",
    launcher_status: "실행 상태",
    returned_session_page: "세션 상태",
    all_returned_sessions: "전체 세션 상태",
  };
  return (
    <section className="connection-strip" id="systems" aria-label="시스템 연결 상태">
      {Object.entries(overview.connection).map(([name, value]) => {
        const meta = connectionMeta(value.state);
        const scope = scopes[value.scope] ?? value.scope.replaceAll("_", " ");
        return <div className="connection-item" key={name} title={`${name} · ${scope}`}><span className={`connection-light tone-${meta.tone}`} /><div><span>{name}</span><strong>{meta.label}</strong><small>{scope}</small></div></div>;
      })}
      <div className="connection-scope"><Icon name="activity" size={15} /><span>인덱스 #{overview.index.through_sequence}</span>{overview.index.catching_up && <StatusBadge tone="warning">동기화 중</StatusBadge>}</div>
    </section>
  );
}

function FilterBar({ filters, overview, modules, onChange, onReset }: {
  filters: DashboardFilters;
  overview: OverviewResponse;
  modules: string[];
  onChange: (filters: DashboardFilters) => void;
  onReset: () => void;
}) {
  const update = <K extends keyof DashboardFilters>(key: K, value: DashboardFilters[K]) => onChange({ ...filters, [key]: value });
  const changed = JSON.stringify(filters) !== JSON.stringify(defaultFilters);
  return (
    <div className="filter-bar">
      <label className="search-field"><Icon name="search" size={16} /><input aria-label="검색" value={filters.query} onChange={(event) => update("query", event.target.value)} placeholder="ID 또는 탐지 이유 검색" /></label>
      <label className="select-field"><span>세션</span><select value={filters.sessionId} onChange={(event) => update("sessionId", event.target.value)}><option value="ALL">전체</option>{overview.sessions.map((session) => <option value={session.id} key={session.id}>{session.id}</option>)}</select></label>
      <label className="select-field"><span>플레이어</span><select value={filters.playerId} onChange={(event) => update("playerId", event.target.value)}><option value="ALL">전체</option>{overview.players.map((player) => <option value={player.id} key={player.id}>{player.display_name}</option>)}</select></label>
      <label className="select-field"><span>모듈</span><select value={filters.module} onChange={(event) => update("module", event.target.value)}><option value="ALL">전체</option>{modules.map((module) => <option value={module} key={module}>{humanizeModule(module)}</option>)}</select></label>
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
      <SectionHeader title="탐지 대상" count={sorted.length} />
      {sorted.length ? <div className="subject-list">{sorted.map((item) => {
        const key = `${item.session_id}:${item.player_id}`;
        const meta = verdictMeta[item.status];
        return <button type="button" className={`subject-row ${selected === key ? "selected" : ""}`} key={key} onClick={() => onSelect(key)}><span className={`subject-state tone-${meta.tone}`}><Icon name={item.status === "SUSPICIOUS" ? "alert" : item.status === "NO_ACTIVE_EVIDENCE" ? "check" : "clock"} size={17} /></span><span className="subject-copy"><strong>{item.player_id}</strong><small>{item.session_id}</small></span><StatusBadge tone={meta.tone} dot={false}>{meta.short}</StatusBadge><Icon name="chevron" size={15} /></button>;
      })}</div> : <EmptyState title="조건에 맞는 대상 없음" />}
    </div>
  );
}

function VerdictView({ assessment, snapshot }: { assessment: Assessment | null; snapshot: SnapshotResponse | null }) {
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
  return (
    <div className="module-state-list">
      <SectionHeader title="모듈 상태" count={modules.length} />
      {modules.length ? modules.map((item) => <article className="module-state-card" key={item.event_id}><div><span className="module-glyph"><Icon name="activity" size={16} /></span><div><strong>{humanizeModule(item.module)}</strong><small>{formatElapsed(item.timestamp_ms)} · #{item.sequence}</small></div></div><span className={`raw-chip ${item.raw_score > 0 ? "active" : ""}`}>raw {item.raw_score}</span>{item.reasons.length > 0 && <p>{item.reasons[0]}</p>}</article>) : <EmptyState title="모듈 상태 없음" />}
    </div>
  );
}

function Timeline({ events, onSelect }: { events: DashboardEvent[]; onSelect: (event: DashboardEvent) => void }) {
  const latest = [...events].sort((a, b) => b.sequence - a.sequence).slice(0, 7);
  return (
    <Panel className="timeline-panel">
      <SectionHeader title="최근 흐름" count={events.length} />
      {latest.length ? <ol className="timeline-list">{latest.map((event) => <li key={event.id}><button type="button" onClick={() => onSelect(event)}><time>{formatElapsed(event.timestamp_ms)}</time><span className={`timeline-dot ${event.raw_score > 0 ? "active" : ""}`} /><span><strong>{humanizeModule(event.module)}</strong><small>{event.reasons[0] ?? (event.event_kind === "operational" ? "운영 상태" : "이벤트")}</small></span><span className="raw-chip">{event.raw_score}</span><Icon name="chevron" size={15} /></button></li>)}</ol> : <EmptyState title="표시할 이벤트 없음" />}
    </Panel>
  );
}

function SubjectSystemStatus({ status }: { status: SubjectStatusResponse | null }) {
  if (!status) return <Panel className="health-panel"><SectionHeader title="실행 상태" /><EmptyState title="상태 데이터 없음" /></Panel>;
  const launcher = connectionMeta(status.launcher.state);
  const components = status.sources.flatMap((source) => source.components.map((component) => ({ ...component, sourceId: source.client_id })));
  const statusReason = status.reason || status.launcher.reason;
  return (
    <Panel className="health-panel">
      <SectionHeader title="실행 상태" action={<StatusBadge tone={launcher.tone}>{launcher.label}</StatusBadge>} />
      <div className="health-summary"><div><span>Launcher</span><strong>{status.launcher.connected ? "연결" : "미연결"}</strong></div><div><span>상태 소스</span><strong>{status.sources.length}{status.has_more_sources ? "+" : ""}</strong></div></div>
      {statusReason && status.state !== "healthy" && <div className="health-note"><Icon name="info" size={14} /><span>{statusReason}</span></div>}
      <div className="component-list">{components.slice(0, 6).map((component) => { const meta = connectionMeta(component.state); return <div className="component-row" key={`${component.sourceId}:${component.id}`}><span className={`connection-light tone-${meta.tone}`} /><div><strong>{humanizeModule(component.id)}</strong><small>{status.sources.length > 1 ? `${component.sourceId} · ` : ""}{formatAge(component.effective_age_ms)}</small></div><span>{meta.label}</span></div>; })}</div>
      {components.length > 6 && <div className="health-more">구성 요소 {components.length - 6}개 더 있음</div>}
      {status.has_more_sources && <div className="health-more">일부 상태 소스만 표시됨</div>}
    </Panel>
  );
}

function EventsTable({ events, page, onPage, onSelect }: { events: DashboardEvent[]; page: number; onPage: (page: number) => void; onSelect: (event: DashboardEvent) => void }) {
  const pageCount = Math.max(1, Math.ceil(events.length / EVENT_PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const visible = events.slice((safePage - 1) * EVENT_PAGE_SIZE, safePage * EVENT_PAGE_SIZE);
  return (
    <Panel id="events" className="events-panel">
        <SectionHeader title="탐지 이벤트" count={events.length} />
      {visible.length ? <><div className="table-wrap"><table><thead><tr><th>시간</th><th>대상</th><th>모듈</th><th>탐지 이유</th><th>Raw</th><th /></tr></thead><tbody>{visible.map((event) => <tr key={event.id} onClick={() => onSelect(event)}><td><span className="time-cell">{formatElapsed(event.timestamp_ms)}</span></td><td><strong>{event.player_id}</strong><small>{event.session_id}</small></td><td><span className="module-pill">{humanizeModule(event.module)}</span></td><td><strong>{event.reasons[0] ?? (event.event_kind === "operational" ? "운영 상태" : "—")}</strong><small>#{event.sequence}</small></td><td><span className={`raw-chip ${event.raw_score > 0 ? "active" : ""}`}>{event.raw_score}</span></td><td><button type="button" className="row-open" aria-label={`${event.player_id} ${humanizeModule(event.module)} 이벤트 #${event.sequence} 상세 보기`}><Icon name="chevron" size={15} /></button></td></tr>)}</tbody></table></div><div className="pagination"><span>{(safePage - 1) * EVENT_PAGE_SIZE + 1}–{Math.min(safePage * EVENT_PAGE_SIZE, events.length)} / {events.length}</span><div><button type="button" onClick={() => onPage(safePage - 1)} disabled={safePage === 1}>이전</button><span>{safePage} / {pageCount}</span><button type="button" onClick={() => onPage(safePage + 1)} disabled={safePage === pageCount}>다음</button></div></div></> : <EmptyState title="조건에 맞는 이벤트 없음" />}
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
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [error, setError] = useState<string | null>(null);
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

  const modules = useMemo(() => [...new Set([...events.map((event) => event.module), ...overview.sessions.flatMap((session) => session.module_ids)])].sort(), [events, overview.sessions]);
  const filteredEvents = useMemo(() => filterEvents(events, overview.assessments, filters), [events, filters, overview.assessments]);
  const filteredAssessments = useMemo(() => filterAssessments(overview.assessments, events, filters), [events, filters, overview.assessments]);
  const selectedAssessment = filteredAssessments.find((item) => item.id === selectedKey) ?? filteredAssessments[0] ?? null;
  const subjectEvents = selectedAssessment ? filteredEvents.filter((event) => event.session_id === selectedAssessment.session_id && event.player_id === selectedAssessment.player_id) : [];
  const selectedSubjectKey = selectedAssessment ? subjectKey(selectedAssessment.session_id, selectedAssessment.player_id) : "";
  const visibleSnapshot = snapshot && subjectKey(snapshot.session_id, snapshot.player_id) === selectedSubjectKey ? snapshot : null;
  const visibleSubjectStatus = subjectStatus && subjectKey(subjectStatus.session_id, subjectStatus.player_id) === selectedSubjectKey ? subjectStatus : null;

  useEffect(() => {
    const assessment = selectedAssessment;
    const requestId = ++detailRequestRef.current;
    const controller = new AbortController();
    setSnapshot(null);
    setSubjectStatus(null);
    if (!assessment) return () => controller.abort();

    const expectedKey = subjectKey(assessment.session_id, assessment.player_id);
    if (mode === "demo") {
      setSnapshot(demoSnapshots[expectedKey] ?? null);
      setSubjectStatus(demoStatuses[expectedKey] ?? null);
      return () => controller.abort();
    }

    void loadSubjectDetail(connection, assessment.session_id, assessment.player_id, controller.signal)
      .then((detail) => {
        if (controller.signal.aborted || requestId !== detailRequestRef.current) return;
        const snapshotKey = subjectKey(detail.snapshot.session_id, detail.snapshot.player_id);
        const statusKey = subjectKey(detail.status.session_id, detail.status.player_id);
        if (snapshotKey !== expectedKey || statusKey !== expectedKey) {
          throw new DashboardApiError("선택한 대상과 다른 상세 응답이 도착했습니다.");
        }
        setSnapshot(detail.snapshot);
        setSubjectStatus(detail.status);
      })
      .catch((caught) => {
        if (controller.signal.aborted || requestId !== detailRequestRef.current) return;
        setError(caught instanceof DashboardApiError ? caught.message : "대상 상세 정보를 불러오지 못했습니다.");
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
      const bundle = await loadDashboardBundle(connection, undefined, afterSequence, controller.signal);
      if (controller.signal.aborted) return;
      setOverview(bundle.overview);
      setLoadWarning(loadStateWarning(bundle));
      setEvents((current) => {
        const merged = new Map(current.map((event) => [event.id, event]));
        for (const event of bundle.events.items) merged.set(event.id, event);
        return [...merged.values()];
      });
      setLastQueryAt(new Date());
    } catch (caught) {
      if (controller.signal.aborted) return;
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
    setSelectedEvent(null);
    connectControllerRef.current?.abort();
    const controller = new AbortController();
    connectControllerRef.current = controller;
    connectInFlightRef.current = true;
    setLoading(true); setError(null);
    try {
      const bundle = await loadDashboardBundle(input, undefined, 0, controller.signal);
      if (controller.signal.aborted) return;
      setConnection(input); setOverview(bundle.overview); setEvents(bundle.events.items); setMode("live"); setFilters(defaultFilters); setLoadWarning(loadStateWarning(bundle));
      setSelectedKey(bundle.overview.assessments[0]?.id ?? ""); setConnectionOpen(false); setLastQueryAt(new Date());
    } catch (caught) {
      if (controller.signal.aborted) return;
      setError(caught instanceof DashboardApiError ? caught.message : "중앙 서버에 연결하지 못했습니다.");
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
    setSelectedEvent(null);
    setOverview(demoOverview); setEvents(demoEvents.items); setMode("demo"); setFilters(defaultFilters);
    setSelectedKey(demoOverview.assessments[0]?.id ?? ""); setError(null); setLoadWarning(null); setLastQueryAt(new Date());
  };

  const openEvent = async (event: DashboardEvent) => {
    eventControllerRef.current?.abort();
    const controller = new AbortController();
    eventControllerRef.current = controller;
    const requestId = ++eventRequestRef.current;
    setSelectedEvent(event);
    if (mode !== "live") return;
    setEventLoading(true);
    try {
      const detail = await loadEventDetail(connection, event.id, controller.signal);
      if (!controller.signal.aborted && requestId === eventRequestRef.current && detail.id === event.id) setSelectedEvent(detail);
    } catch (caught) {
      if (!controller.signal.aborted && requestId === eventRequestRef.current) setError(caught instanceof DashboardApiError ? caught.message : "이벤트 상세를 불러오지 못했습니다.");
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

  const suspiciousCount = overview.assessments.filter((item) => item.status === "SUSPICIOUS").length;

  return (
    <div className="app-shell">
      <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} />
      <main className="main-shell">
        <header className="topbar">
          <div className="topbar-title"><button className="menu-button" type="button" onClick={() => setMenuOpen(true)} aria-label="메뉴 열기"><Icon name="menu" /></button><div><strong>탐지 현황</strong></div></div>
          <div className="topbar-actions">
            <StatusBadge tone={mode === "demo" ? "info" : "success"}>{mode === "demo" ? "DEMO" : "LIVE"}</StatusBadge>
            <span className="last-query">{new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(lastQueryAt)}</span>
            {mode === "live" && <label className="auto-toggle"><input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} /><span aria-hidden="true" /><b>5초 갱신</b></label>}
            <button className="icon-button" type="button" onClick={() => void refresh()} disabled={refreshing} aria-label="새로고침"><Icon name="refresh" className={refreshing ? "spin" : ""} /></button>
            {mode === "live" && <button className="button button-quiet desktop-action" type="button" onClick={useDemo}>시연 보기</button>}
            <button className="button button-primary" type="button" onClick={() => setConnectionOpen(true)}><Icon name="server" />연결</button>
          </div>
        </header>

        <div className="content-shell" id="overview">
          {error && <div className="error-banner" role="alert"><Icon name="alert" /><span>{error}</span><button type="button" onClick={() => setError(null)} aria-label="오류 닫기"><Icon name="close" size={16} /></button></div>}
          {loadWarning && <div className="error-banner warning-banner" role="status"><Icon name="info" /><span>{loadWarning}</span><button type="button" onClick={() => setLoadWarning(null)} aria-label="안내 닫기"><Icon name="close" size={16} /></button></div>}
          <ConnectionStrip overview={overview} />
          <FilterBar filters={filters} overview={overview} modules={modules} onChange={setFilters} onReset={() => setFilters(defaultFilters)} />

          <section className="summary-grid" aria-label="요약">
            <SummaryCard icon="timeline" label="세션" value={overview.counts.sessions} tone="blue" />
            <SummaryCard icon="user" label="플레이어" value={overview.counts.players} tone="violet" />
            <SummaryCard icon="database" label="이벤트" value={overview.counts.events} tone="navy" />
            <SummaryCard icon="alert" label="의심 판정" value={suspiciousCount} tone="red" />
          </section>

          <Panel id="subjects" className="workspace-panel">
            <div className="workspace-grid"><SubjectList assessments={filteredAssessments} selected={selectedAssessment?.id ?? ""} onSelect={setSelectedKey} /><VerdictView assessment={selectedAssessment} snapshot={visibleSnapshot} /><ModuleStateList snapshot={visibleSnapshot} /></div>
          </Panel>

          <div className="insight-grid"><Timeline events={subjectEvents} onSelect={(event) => void openEvent(event)} /><SubjectSystemStatus status={visibleSubjectStatus} /></div>
          <EventsTable events={filteredEvents} page={page} onPage={setPage} onSelect={(event) => void openEvent(event)} />
        </div>
      </main>

      {(loading || refreshing) && <div className="refresh-indicator" aria-live="polite"><span className="spinner" />{loading ? "연결 중" : "새로고침 중"}</div>}
      <EvidenceDrawer event={selectedEvent} loading={eventLoading} evidenceImagesAvailable={overview.capabilities.evidence_images} onClose={() => { eventControllerRef.current?.abort(); eventRequestRef.current += 1; setEventLoading(false); setSelectedEvent(null); }} />
      <ConnectionDialog open={connectionOpen} loading={loading} initial={connection} onClose={() => setConnectionOpen(false)} onConnect={(input) => void connect(input)} />
    </div>
  );
}
