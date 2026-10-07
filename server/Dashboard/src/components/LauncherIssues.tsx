import { componentMeta, connectionMeta } from "../domain";
import { launcherIssueMeta, launcherIssues } from "../launcherIssues";
import { redactSensitiveText } from "./EvidenceDrawer";
import type { LauncherOverviewStatus, ModuleStatus } from "../types";
import { StatusBadge } from "./StatusBadge";

export interface LauncherIssuesProps {
  launcherStatuses: readonly LauncherOverviewStatus[];
  moduleStatuses: readonly ModuleStatus[];
  onSubject?: (sessionId: string, playerId: string) => void;
}

export function LauncherIssues({ launcherStatuses, moduleStatuses, onSubject }: LauncherIssuesProps) {
  const issues = launcherIssues(launcherStatuses, moduleStatuses);
  return (
    <section className="launcher-issues" aria-label="Launcher 점검 대상">
      <div className="section-header"><h3>Launcher 점검 대상</h3><span className="count-label">{issues.length}건</span></div>
      {issues.length ? <div className="table-wrap"><table className="data-table">
        <thead><tr><th>세션 / 플레이어</th><th>클라이언트</th><th>모듈</th><th>점검 항목</th><th>현재 상태</th><th>Launcher 상태</th><th>상세</th></tr></thead>
        <tbody>{issues.map((issue) => {
          const issueMeta = launcherIssueMeta(issue.kind);
          const currentMeta = connectionMeta(issue.state);
          const reportedMeta = issue.reportedStatus ? componentMeta[issue.reportedStatus] : null;
          return <tr key={issue.key}>
            <td><div className="cell-stack">{onSubject ? <button className="table-link" type="button" onClick={() => onSubject(issue.sessionId, issue.playerId)} aria-label={`${redactSensitiveText(issue.sessionId)} ${redactSensitiveText(issue.playerId)} 상세 보기`}>{redactSensitiveText(issue.sessionId)}</button> : <strong>{redactSensitiveText(issue.sessionId)}</strong>}<small>{redactSensitiveText(issue.playerId)}</small></div></td>
            <td className="mono">{issue.clientId ? redactSensitiveText(issue.clientId) : "미보고"}</td>
            <td><div className="cell-stack"><strong>{issue.label}</strong><small>{issue.required === null ? "—" : issue.required ? "필수" : "선택"}</small></div></td>
            <td><StatusBadge tone={issueMeta.tone}>{issueMeta.label}</StatusBadge></td>
            <td><div className="cell-stack"><StatusBadge tone={currentMeta.tone}>{currentMeta.label}</StatusBadge>{reportedMeta && issue.reportedStatus !== issue.state && <small>마지막 보고: {reportedMeta.label}</small>}</div></td>
            <td className="mono">{issue.launcherStatus ?? "—"}</td>
            <td><div className="cell-stack">{issue.reason && <span>{issue.reason}</span>}{Object.entries(issue.details).map(([key, value]) => <small className="mono" key={key}>{key}: {String(value)}</small>)}{!issue.reason && Object.keys(issue.details).length === 0 && <span>추가 근거 미제공</span>}</div></td>
          </tr>;
        })}</tbody>
      </table></div> : <div className="empty-state"><strong>점검 대상 없음</strong></div>}
    </section>
  );
}
