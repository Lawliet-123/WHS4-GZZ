import { eventTime } from "../eventTime";
import type { DashboardEvent } from "../types";

/** Never equate server receipt time with the detector's observation time. */
export function EventTimeLabel({ event }: { event: DashboardEvent }) {
  const time = eventTime(event);
  return <span className="event-time" title={`${time.primaryLabel} · ${time.secondaryLabel ?? "실제 시각 미제공"}`}>
    <span className="time-cell">{time.primaryLabel}</span>
    <small>{time.secondaryLabel ?? "실제 시각 미제공"}</small>
  </span>;
}
