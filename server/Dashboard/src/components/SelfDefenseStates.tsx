import { humanizeModule } from "../domain";
import type { DashboardEvent, SelfDefenseStatus } from "../types";
import { redactEventText } from "./EvidenceDrawer";
import { EventTimeLabel } from "./EventTimeLabel";
import { StatusBadge } from "./StatusBadge";

/** The overview projection has no clock metadata; only the original Event may supply it. */
export function selfDefenseEvent(item: SelfDefenseStatus, events: readonly DashboardEvent[]): DashboardEvent {
  const original = events.find((event) => event.id === item.event_id && event.sequence === item.sequence
    && event.session_id === item.session_id && event.player_id === item.player_id
    && event.module === "selfdefense" && event.timestamp_ms === item.timestamp_ms && event.raw_score === item.raw_score);
  return original ?? {
    id: item.event_id, sequence: item.sequence, session_id: item.session_id, player_id: item.player_id,
    module: "selfdefense", timestamp_ms: item.timestamp_ms, raw_score: item.raw_score,
    evidence: item.evidence, reasons: item.reasons, event_kind: "operational", time_basis: "unknown",
    observed_at_utc: null, received_at_utc: null, evidence_image: null, log_excerpt: null,
  };
}

function statusTone(value: string | null): string {
  const status = value?.toUpperCase();
  if (["ERROR", "FAILED", "DETECTED", "CRASHED"].includes(status ?? "")) return "danger";
  if (["WARN", "WARNING", "SUSPICIOUS", "OFFLINE", "DEGRADED", "INCOMPLETE"].includes(status ?? "")) return "warning";
  if (["NORMAL", "CLEAN", "HEALTHY", "OK"].includes(status ?? "")) return "success";
  return "neutral";
}

const kindLabels: Record<string, string> = {
  module_health: "Watchdog", file_integrity: "파일 무결성", debugger_presence: "디버거 연결",
};
const kindLabel = (kind: string) => Object.hasOwn(kindLabels, kind) ? kindLabels[kind]! : kind;

export function SelfDefenseStates({ items, events, onSelect, issuesOnly = false }: {
  items: readonly SelfDefenseStatus[] | undefined;
  events: readonly DashboardEvent[];
  onSelect?: (event: DashboardEvent) => void;
  issuesOnly?: boolean;
}) {
  const visible = items?.filter((item) => !issuesOnly || statusTone(item.status) !== "success" || item.scan_complete !== true);
  return <section className="selfdefense-states" aria-label="SelfDefense 운영 상태">
    <div className="module-signals-head"><h3>SelfDefense 운영 상태</h3><span className="muted">플레이어 판정과 별개</span></div>
    {visible?.length ? <div className="table-scroll"><table className="module-signals-table" aria-label="SelfDefense 기능별 상태"><thead><tr>
      <th>플레이어 / 세션</th><th>기능 / 대상 모듈</th><th>운영 상태</th><th>검사 완료</th><th>검사 범위 / 근거</th><th>원본 기록</th>
    </tr></thead><tbody>{visible.map((item) => {
      const event = selfDefenseEvent(item, events);
      const safe = (value: string) => redactEventText(event, value);
      return <tr key={JSON.stringify([item.session_id, item.player_id, item.kind, item.target_module, item.event_id])}>
        <td><strong>{safe(item.player_id)}</strong><small className="mono">{safe(item.session_id)}</small></td>
        <td><strong>{safe(kindLabel(item.kind))}</strong><small>{item.target_module ? safe(humanizeModule(item.target_module)) : item.component ? safe(item.component) : "대상 미제공"}</small></td>
        <td><StatusBadge tone={statusTone(item.status)}>{item.status ? safe(item.status) : "미제공"}</StatusBadge></td>
        <td><StatusBadge tone={item.scan_complete === false ? "warning" : "neutral"}>{item.scan_complete === null ? "미제공" : item.scan_complete ? "완료" : "미완료"}</StatusBadge></td>
        <td className="policy-reason"><strong>{item.scope ? safe(item.scope) : "범위 미제공"}</strong><small>{item.reasons.length ? safe(item.reasons.join(" · ")) : "근거 미제공"}</small></td>
        <td><span className="mono">raw {item.raw_score} · #{item.sequence}</span><EventTimeLabel event={event} />{onSelect && <button type="button" className="text-button" aria-label={`${safe(item.player_id)} ${safe(kindLabel(item.kind))} ${item.target_module ? safe(humanizeModule(item.target_module)) : ""} 운영 기록 상세`.replace(/\s+/g, " ")} onClick={() => onSelect(event)}>상세</button>}</td>
      </tr>;
    })}</tbody></table></div> : <div className="empty-inline">{items === undefined ? "SelfDefense 상태 API 미제공" : issuesOnly ? "점검할 SelfDefense 상태 없음" : "SelfDefense 운영 기록 없음"}</div>}
  </section>;
}
