import { eventTime } from "../eventTime";
import type { DashboardEvent } from "../types";

/** Never equate server receipt time with the detector's observation time. */
export function EventTimeLabel({ event }: { event: DashboardEvent }) {
  const time = eventTime(event);
  const receipt = time.receivedLabel !== null ? `수신 ${time.receivedLabel}` : null;
  return <span className="event-time" title={[time.primaryLabel, time.secondaryLabel, receipt].filter(Boolean).join(" · ")}>
    <span className="time-cell">{time.primaryLabel}</span>
    <small title={time.observedLabel !== null ? "생산자가 선언한 시계 기준. 서버 시계 검증 결과가 아닙니다." : undefined}>{time.secondaryLabel}</small>
    {receipt && <small>{receipt}</small>}
  </span>;
}
