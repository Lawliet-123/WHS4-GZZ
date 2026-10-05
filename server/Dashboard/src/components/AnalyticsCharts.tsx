import { useId, useMemo, useState } from "react";
import { detectorObservationCounts, sessionObservationFlow, verdictDistribution } from "../chartData";
import { verdictMeta } from "../domain";
import type { Assessment, DashboardEvent, VerdictStatus } from "../types";
import "./AnalyticsCharts.css";

export function OverviewAnalytics({ assessments, events, onVerdict, onDetector }: {
  assessments: Assessment[];
  events: DashboardEvent[];
  onVerdict: (status: VerdictStatus) => void;
  onDetector: (id: string) => void;
}) {
  const distribution = useMemo(() => verdictDistribution(assessments), [assessments]);
  const detectors = useMemo(() => detectorObservationCounts(events), [events]);
  const total = distribution.reduce((sum, item) => sum + item.count, 0);
  const observations = detectors.reduce((sum, item) => sum + item.count, 0);
  const ringLabelId = useId();
  const circumference = 2 * Math.PI * 57;
  let offset = 0;
  const maximum = Math.max(1, ...detectors.map((item) => item.count));
  return <div className="overview-analytics">
    <section className="analytics-panel" aria-labelledby={ringLabelId}>
      <header className="analytics-header"><h2 id={ringLabelId}>대상 판정 분포</h2><span className="analytics-scope" title="불러온 세션·플레이어 쌍 기준">{total} 대상</span></header>
      <div className="verdict-chart-body">
        <svg className="verdict-ring" viewBox="0 0 160 160" role="img" aria-label={total ? `세션·플레이어 판정: ${distribution.map((item) => `${item.status} ${item.count}대상`).join(", ")}` : "판정 데이터 없음"}>
          <circle className="ring-track" cx="80" cy="80" r="57" />
          {distribution.map((item) => {
            const length = total ? item.count / total * circumference : 0;
            const segmentOffset = offset;
            offset += length;
            return item.count > 0 ? <circle key={item.status} className={`ring-segment verdict-${item.status}`} cx="80" cy="80" r="57" strokeDasharray={`${length} ${circumference - length}`} strokeDashoffset={-segmentOffset} transform="rotate(-90 80 80)" /> : null;
          })}
          <text className="ring-total" x="80" y="79" textAnchor="middle">{total.toLocaleString("ko-KR")}</text>
          <text className="ring-unit" x="80" y="99" textAnchor="middle">{total ? "불러온 대상" : "데이터 없음"}</text>
        </svg>
        <div className="chart-legend">{distribution.map((item) => <button className="chart-legend-item" type="button" key={item.status} disabled={!item.count} onClick={() => onVerdict(item.status)} aria-label={`${item.status} ${item.count}대상 보기`} title={item.status}>
          <span className={`chart-swatch verdict-${item.status}`} aria-hidden="true" /><span className="chart-label"><strong>{verdictMeta[item.status].label}</strong><small>{item.status}</small></span><span className="chart-count">{item.count}</span>
        </button>)}</div>
      </div>
    </section>
    <section className="analytics-panel" aria-label="탐지기별 관측">
      <header className="analytics-header"><h2>탐지기별 관측</h2><span className="analytics-scope">불러온 {observations}건</span></header>
      {detectors.length ? <div className="detector-chart-body">
        {detectors.slice(0, 6).map((item) => <button key={item.id} type="button" className="detector-chart-row" onClick={() => onDetector(item.id)} aria-label={`${item.label} 관측 ${item.count}건 보기`} title={`${item.label} · ${item.count}건 (0점 포함)`}>
          <span className="detector-chart-label">{item.label}</span><span className="detector-bar-track" aria-hidden="true"><span className="detector-bar" style={{ width: `${item.count / maximum * 100}%` }} /></span><span className="chart-count">{item.count}</span>
        </button>)}
        <div className="chart-axis"><span>0점 포함 · 관측 건수</span><span>0 — {maximum}</span></div>
        {detectors.length > 6 && <span className="chart-more">상위 6 / {detectors.length} 탐지기</span>}
      </div> : <div className="chart-empty">관측 데이터 없음</div>}
    </section>
  </div>;
}

export function SessionObservationChart({ events, sessionIds, onEvents }: {
  events: DashboardEvent[];
  sessionIds: string[];
  onEvents: (sessionId: string) => void;
}) {
  const [choice, setChoice] = useState<string | null>(null);
  const [activeBin, setActiveBin] = useState<{ sessionId: string; index: number } | null>(null);
  const latestSession = [...events].filter((event) => event.event_kind === "detection" && sessionIds.includes(event.session_id)).sort((a, b) => b.sequence - a.sequence)[0]?.session_id;
  const sessionId = choice && sessionIds.includes(choice) ? choice : latestSession ?? sessionIds[0] ?? "";
  const bins = useMemo(() => sessionObservationFlow(events, sessionId), [events, sessionId]);
  const titleId = useId();
  const total = bins.reduce((sum, bin) => sum + bin.count, 0);
  const maxCount = Math.max(2, ...bins.map((bin) => bin.count));
  const plot = { left: 35, right: 744, top: 12, bottom: 145 };
  const width = plot.right - plot.left;
  const height = plot.bottom - plot.top;
  const columnWidth = width / Math.max(1, bins.length);
  const y = (count: number) => plot.bottom - count / maxCount * height;
  const active = activeBin?.sessionId === sessionId ? bins[activeBin.index] : null;
  return <section className="analytics-panel session-flow-panel" aria-labelledby={titleId}>
    <header className="analytics-header"><h2 id={titleId}>세션 관측 흐름</h2><label className="chart-session-select"><span>세션</span><select aria-label="그래프 세션" value={sessionId} disabled={!sessionIds.length} onChange={(event) => { setChoice(event.target.value); setActiveBin(null); }}>{sessionIds.length ? sessionIds.map((id) => <option key={id} value={id}>{id}</option>) : <option value="">세션 없음</option>}</select></label></header>
    {bins.length ? <div className="flow-chart-body">
      <div className="flow-chart-legend"><span><i className="chart-swatch flow-all" />0점 이하</span><span title="raw_score > 0 관측. 최종 판정은 Scoring에서 별도로 확인합니다."><i className="chart-swatch flow-positive-swatch" />양수 관측</span><span className="analytics-scope">불러온 {total}건</span></div>
      <svg className="flow-chart" viewBox="0 0 760 185" role="group" aria-label={`${sessionId} 서버 수신 순서별 관측 건수`}>
        {[0, Math.floor(maxCount / 2), maxCount].map((count) => <g key={count} aria-hidden="true"><line className="flow-grid" x1={plot.left} x2={plot.right} y1={y(count)} y2={y(count)} /><text className="flow-axis-label" x={plot.left - 9} y={y(count) + 4} textAnchor="end">{count}</text></g>)}
        {bins.map((bin, index) => {
          const x = plot.left + index * columnWidth + columnWidth * 0.23;
          const barWidth = columnWidth * 0.54;
          const label = bin.sequenceStart === bin.sequenceEnd ? `#${bin.sequenceStart}` : `#${bin.sequenceStart}–${bin.sequenceEnd}`;
          const description = `${label} · ${bin.count}건 · 양수 ${bin.positiveCount}건`;
          return <g className="flow-bin" key={`${sessionId}:${bin.sequenceStart}:${bin.sequenceEnd}`} tabIndex={0} role="img" aria-label={description} onMouseEnter={() => setActiveBin({ sessionId, index })} onMouseLeave={() => setActiveBin(null)} onFocus={() => setActiveBin({ sessionId, index })} onBlur={() => setActiveBin(null)}>
            <title>{description}</title><rect className="flow-hit" x={plot.left + index * columnWidth} y={plot.top} width={columnWidth} height={height} />
            <rect className="flow-bar" x={x} y={y(bin.count)} width={barWidth} height={bin.count / maxCount * height} />
            {bin.positiveCount > 0 && <rect className="flow-positive" x={x} y={y(bin.positiveCount)} width={barWidth} height={bin.positiveCount / maxCount * height} />}
            <text className="flow-axis-label" x={x + barWidth / 2} y={y(bin.count) - 5} textAnchor="middle">{bin.count}</text>
            {(index === 0 || index === bins.length - 1 || index % Math.ceil(bins.length / 4) === 0) && <text className="flow-axis-label" x={x + barWidth / 2} y="164" textAnchor="middle">#{bin.sequenceStart}</text>}
          </g>;
        })}
        <text className="flow-axis-label" x={plot.right} y="181" textAnchor="end">서버 수신 순서</text>
      </svg>
      <footer className="flow-footer"><output aria-live="polite">{active ? `#${active.sequenceStart}–${active.sequenceEnd} · ${active.count}건 · 양수 ${active.positiveCount}건` : `수신 #${bins[0]!.sequenceStart}–#${bins.at(-1)!.sequenceEnd}`}</output><button type="button" className="page-link" onClick={() => onEvents(sessionId)}>이 세션 이벤트 →</button></footer>
    </div> : <div className="chart-empty">이 세션의 관측 데이터 없음</div>}
  </section>;
}
