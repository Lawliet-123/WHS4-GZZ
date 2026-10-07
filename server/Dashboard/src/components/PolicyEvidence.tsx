import { useMemo, useState } from "react";
import { humanizeModule } from "../domain";
import { policyEvidenceRows, representativeReason, sortModuleSignals } from "../policyEvidence";
import type { ModuleSignalSort, PolicyEvidenceRow, PolicySignalStatus } from "../policyEvidence";
import type { DashboardEvent, SnapshotResponse } from "../types";
import { redactSensitiveText } from "./EvidenceDrawer";
import { EventTimeLabel } from "./EventTimeLabel";
import { StatusBadge } from "./StatusBadge";

export interface PolicyEvidenceProps {
  snapshot: SnapshotResponse | null;
  events: readonly DashboardEvent[];
  onSelect?: (event: DashboardEvent) => void;
}

const signalMeta: Record<PolicySignalStatus, { label: string; tone: string }> = {
  ACTIVE: { label: "현재 활성", tone: "danger" },
  INACTIVE: { label: "현재 비활성", tone: "neutral" },
  ADVISORY: { label: "보조 근거", tone: "info" },
  DEFERRED: { label: "추가 이력 필요", tone: "warning" },
  UNRESOLVED: { label: "정책 미확정", tone: "warning" },
  UNAVAILABLE: { label: "관측 불가", tone: "warning" },
  UNKNOWN: { label: "상태 미제공", tone: "neutral" },
};
const raw = (value: number | null | undefined) => value ?? "미제공";

function EventLink({ event, onSelect, label }: { event: DashboardEvent | null; onSelect?: PolicyEvidenceProps["onSelect"]; label: string }) {
  if (!event) return <span className="muted">미제공</span>;
  const content = <><span className="mono">raw {event.raw_score}</span><EventTimeLabel event={event} /></>;
  return onSelect
    ? <button type="button" className="text-button policy-event-link" onClick={() => onSelect(event)} aria-label={`${label} 상세`}>{content}</button>
    : <span className="policy-event-link">{content}</span>;
}

function PolicyState({ row }: { row: PolicyEvidenceRow }) {
  const meta = signalMeta[row.status];
  return <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>;
}

export function VerdictEvidence({ snapshot, events, onSelect }: PolicyEvidenceProps) {
  const rows = useMemo(() => policyEvidenceRows(snapshot, events), [snapshot, events]);
  const activeNames = snapshot?.final_verdict?.active_modules ?? [];
  // Policy explanation and Final Verdict are separate read projections. A
  // policy ACTIVE row must not issue an absent verdict or explain a different
  // verdict generation as though the two reads were an atomic snapshot.
  const active = snapshot?.final_verdict
    ? rows.filter((row) => row.authoritative && row.status === "ACTIVE" && activeNames.includes(row.module))
    : [];
  const other = rows.filter((row) => row.status !== "ACTIVE");
  return <section className="policy-evidence" aria-label="플레이어 판정 근거">
    <div className="policy-evidence-summary"><span>활성 모듈</span><strong>{snapshot?.final_verdict
      ? activeNames.length ? activeNames.map((module) => redactSensitiveText(humanizeModule(module))).join(" · ") : "없음"
      : "미제공"}</strong></div>
    <h4>현재 활성 판정 근거</h4>
    {active.length ? <div className="table-scroll"><table className="policy-evidence-table"><thead><tr>
      <th>모듈 / 채널</th><th>최신 관측</th><th>판정 기준</th><th>세션 보존 사건</th><th>대표 근거</th>
    </tr></thead><tbody>{active.map((row) => <tr key={row.key}>
      <td><strong>{row.label}</strong><PolicyState row={row} /></td>
      <td><EventLink event={row.latest} onSelect={onSelect} label={`${row.label} 최신 관측`} /></td>
      <td className="mono">{row.threshold === null ? "미제공" : `raw ≥ ${row.threshold}`}<small>{row.thresholdMode === "event_threshold" ? "사건 기준" : row.thresholdMode === "threshold" ? "모듈 기준" : ""}</small></td>
      <td>{row.retained ? <EventLink event={row.retained} onSelect={onSelect} label={`${row.label} 세션 보존 사건`} /> : <span className="muted">해당 없음</span>}</td>
      <td className="policy-reason">{representativeReason(row)}</td>
    </tr>)}</tbody></table></div>
      : <div className="empty-inline">{activeNames.length ? "활성 모듈 상세 근거 미제공" : snapshot?.final_verdict ? "현재 활성 근거 없음" : "판정 근거 미제공"}</div>}
    {other.length > 0 && <details className="policy-secondary"><summary>현재 비활성 · 미확정 신호 {other.length}</summary>
      <ul>{other.map((row) => <li key={row.key}><strong>{row.label}</strong><PolicyState row={row} /><span>최신 raw {raw(row.latest?.raw_score)} · 기준 {raw(row.threshold)}</span></li>)}</ul>
    </details>}
  </section>;
}

export function ModuleSignals({ snapshot, events, onSelect }: PolicyEvidenceProps) {
  const [sort, setSort] = useState<ModuleSignalSort>("latest");
  const rows = useMemo(() => sortModuleSignals(policyEvidenceRows(snapshot, events), sort), [snapshot, events, sort]);
  return <section className="module-signals" aria-label="모듈 신호 목록">
    <div className="module-signals-head"><h4>모듈 신호</h4><label>정렬 <select aria-label="모듈 신호 정렬" value={sort} onChange={(event) => setSort(event.target.value as ModuleSignalSort)}>
      <option value="latest">최신 raw 높은 순</option><option value="highest">불러온 이력 최고 raw 높은 순</option>
    </select></label></div>
    {rows.length ? <div className="table-scroll"><table className="module-signals-table"><thead><tr>
      <th>모듈 / 채널</th><th>현재 정책 상태</th><th>최신 raw</th><th>불러온 이력 최고 raw</th><th>최신 관측 근거</th><th>상세</th>
    </tr></thead><tbody>{rows.map((row) => <tr key={row.key}>
      <td>{row.label}</td><td><PolicyState row={row} /></td><td className="mono">{raw(row.latest?.raw_score)}</td>
      <td>{row.highestObserved ? <EventLink event={row.highestObserved} onSelect={onSelect} label={`${row.label} 이력 최고 관측`} /> : <span className="muted">미제공</span>}</td>
      <td className="policy-reason">{representativeReason({ ...row, retained: null })}</td>
      <td>{onSelect && row.latest ? <button type="button" className="text-button" onClick={() => onSelect(row.latest!)} aria-label={`${row.label} 최신 신호 상세`}>최신 관측</button> : "—"}</td>
    </tr>)}</tbody></table></div> : <div className="empty-inline">모듈 신호 없음</div>}
  </section>;
}
