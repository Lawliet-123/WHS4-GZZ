import type { DashboardEvent } from "./types";

/** These labels describe different clocks; receipt time is never observation time. */
export interface EventTimePresentation {
  elapsedLabel: string | null;
  rawLabel: string;
  observedLabel: string | null;
  receivedLabel: string | null;
  primaryLabel: string;
  secondaryLabel: string;
}

type TimedEvent = Pick<DashboardEvent, "timestamp_ms" | "time_basis"> &
  Partial<Pick<DashboardEvent, "received_at_utc" | "observed_at_utc">>;

const kst = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});

/** Require an explicit UTC zone and reject dates JavaScript would normalize. */
export function utcTimestamp(value: string | null | undefined): number | null {
  if (typeof value !== "string") return null;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|\+00:00)$/.exec(value);
  if (!parts) return null;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return null;
  const date = new Date(parsed);
  if ([date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate(),
    date.getUTCHours(), date.getUTCMinutes(), date.getUTCSeconds()]
    .some((component, index) => component !== Number(parts[index + 1]))) return null;
  return parsed;
}

function formatKst(milliseconds: number): string | null {
  const date = new Date(milliseconds);
  if (!Number.isFinite(date.getTime()) || date.getUTCFullYear() < 1 || date.getUTCFullYear() > 9999) return null;
  const values = Object.fromEntries(kst.formatToParts(date).map((part) => [part.type, part.value]));
  const fraction = String(date.getUTCMilliseconds()).padStart(3, "0");
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}.${fraction} KST`;
}

function formatElapsed(milliseconds: number): string {
  const seconds = Math.floor(milliseconds / 1_000);
  const minutes = Math.floor(seconds / 60);
  const remainder = String(seconds % 60).padStart(2, "0");
  const fraction = String(milliseconds % 1_000).padStart(3, "0");
  return `경과 ${String(minutes).padStart(2, "0")}:${remainder}.${fraction}`;
}

/**
 * Only an explicit server time basis permits elapsed or epoch interpretation.
 * Unknown legacy timestamps remain raw values. Neither generated_at_utc nor
 * another event/heartbeat can be used to infer a session start or observation.
 * The Server projects evidence declarations; never reinterpret its unknown
 * result or calculate a new client clock here.
 */
export function eventTime(event: TimedEvent): EventTimePresentation {
  const validTimestamp = Number.isSafeInteger(event.timestamp_ms) && event.timestamp_ms >= 0;
  const rawLabel = typeof event.timestamp_ms === "number" && Number.isFinite(event.timestamp_ms)
    ? `timestamp ${event.timestamp_ms} ms` : "timestamp 미제공";
  const elapsedLabel = validTimestamp && event.time_basis === "session_relative"
    ? formatElapsed(event.timestamp_ms) : null;
  const observed = event.time_basis === "session_relative" && validTimestamp
    ? utcTimestamp(event.observed_at_utc) : null;
  const observedLabel = event.time_basis === "unix_epoch_ms" && validTimestamp
    ? formatKst(event.timestamp_ms)
    : observed !== null ? formatKst(observed) : null;
  const received = utcTimestamp(event.received_at_utc);
  const receivedLabel = received === null ? null : formatKst(received);
  const primaryLabel = elapsedLabel ?? (observedLabel !== null ? `관측 ${observedLabel}` : rawLabel);
  const secondaryLabel = observedLabel !== null && elapsedLabel !== null
    ? `관측 ${observedLabel}`
    : observedLabel !== null ? rawLabel : "관측 시각 미제공";
  return { elapsedLabel, rawLabel, observedLabel, receivedLabel, primaryLabel, secondaryLabel };
}
