import { describe, expect, it } from "vitest";
import { eventTime, utcTimestamp } from "./eventTime";

describe("trustworthy event time labels", () => {
  it("uses T+ only for an explicit session-relative basis", () => {
    expect(eventTime({ timestamp_ms: 84_999, time_basis: "session_relative" }).primaryLabel).toBe("T+01:24");
    expect(eventTime({ timestamp_ms: 3_684_000, time_basis: "session_relative" }).primaryLabel).toBe("T+01:01:24");
    const unknown = eventTime({ timestamp_ms: 84_000, time_basis: "unknown" });
    expect(unknown.elapsedLabel).toBeNull();
    expect(unknown.primaryLabel).toBe("timestamp 84000 ms");
    expect(unknown.secondaryLabel).toBe("실제 시각 미제공");
  });

  it("shows genuine receipt time separately and handles KST midnight", () => {
    const result = eventTime({ timestamp_ms: 84_000, time_basis: "session_relative",
      received_at_utc: "2026-10-06T15:00:00.000Z" });
    expect(result.primaryLabel).toBe("T+01:24");
    expect(result.secondaryLabel).toBe("수신 2026-10-07 00:00:00 KST");
    expect(result.observedLabel).toBeNull();
  });

  it("does not mistake receipt for observation or unknown timestamp for epoch", () => {
    const result = eventTime({ timestamp_ms: 1_791_292_000_000, time_basis: "unknown",
      received_at_utc: "2026-10-06T12:00:02+00:00" });
    expect(result.observedLabel).toBeNull();
    expect(result.elapsedLabel).toBeNull();
    expect(result.secondaryLabel).toBe("수신 2026-10-06 21:00:02 KST");
  });

  it("supports explicit future observation/epoch contracts without guessing", () => {
    expect(eventTime({ timestamp_ms: 84_000, time_basis: "observed_utc",
      observed_at_utc: "2026-10-06T12:00:00Z" }).primaryLabel).toBe("2026-10-06 21:00:00 KST");
    const relative = eventTime({ timestamp_ms: 84_000, time_basis: "session_relative",
      observed_at_utc: "2026-10-06T12:00:00Z" });
    expect(relative.primaryLabel).toBe("T+01:24");
    expect(relative.secondaryLabel).toBe("관측 2026-10-06 21:00:00 KST");
    const epoch = eventTime({ timestamp_ms: 0, time_basis: "unix_epoch_ms" });
    expect(epoch.primaryLabel).toBe("1970-01-01 09:00:00 KST");
    expect(epoch.secondaryLabel).toBe("timestamp 0 ms");
    expect(eventTime({ timestamp_ms: 84_000, time_basis: "unknown",
      observed_at_utc: "2026-10-06T12:00:00Z" }).secondaryLabel).toBe("timestamp 84000 ms");
  });

  it("rejects invalid and ambiguous dates instead of trusting host timezone", () => {
    for (const value of [undefined, null, "", "not-a-date", "2026-02-30T12:00:00Z",
      "2026-10-06T24:00:00Z", "2026-10-06T12:00:00", "2026-10-06T12:00:00+09:00"]) {
      expect(utcTimestamp(value)).toBeNull();
      expect(eventTime({ timestamp_ms: 10, time_basis: "unknown", received_at_utc: value }).receivedLabel).toBeNull();
    }
  });

  it("retains old backend compatibility and rejects invalid elapsed values", () => {
    for (const timestamp_ms of [NaN, Infinity, -1, 0.5, Number.MAX_SAFE_INTEGER + 1]) {
      expect(eventTime({ timestamp_ms, time_basis: "session_relative" }).elapsedLabel).toBeNull();
    }
    expect(eventTime({ timestamp_ms: 0, time_basis: "session_relative" }).primaryLabel).toBe("T+00:00");
  });
});
