import { describe, expect, it } from "vitest";
import { eventTime, utcTimestamp } from "./eventTime";

describe("Dashboard-A event clocks", () => {
  it("preserves milliseconds and formats elapsed minutes without treating them as dates", () => {
    expect(eventTime({ timestamp_ms: 246_771, time_basis: "session_relative" }).primaryLabel).toBe("경과 04:06.771");
    expect(eventTime({ timestamp_ms: 84_999, time_basis: "session_relative" }).primaryLabel).toBe("경과 01:24.999");
    expect(eventTime({ timestamp_ms: 3_684_000, time_basis: "session_relative" }).primaryLabel).toBe("경과 61:24.000");
    expect(eventTime({ timestamp_ms: 0, time_basis: "session_relative" }).primaryLabel).toBe("경과 00:00.000");
  });

  it("renders the contract example in KST without recomputing the producer clock", () => {
    const result = eventTime({ timestamp_ms: 246_771, time_basis: "session_relative",
      observed_at_utc: "2026-10-07T10:12:44.957Z", received_at_utc: "2026-10-07T10:12:45.012Z" });
    expect(result.primaryLabel).toBe("경과 04:06.771");
    expect(result.secondaryLabel).toBe("관측 2026-10-07 19:12:44.957 KST");
    expect(result.receivedLabel).toBe("2026-10-07 19:12:45.012 KST");
    expect(new Date(1_791_367_718_186 + 246_771).toISOString()).toBe("2026-10-07T10:12:44.957Z");
  });

  it("keeps missing observation explicit even when receipt exists, including KST midnight", () => {
    const result = eventTime({ timestamp_ms: 84_000, time_basis: "session_relative",
      received_at_utc: "2026-10-06T15:00:00.007Z" });
    expect(result.primaryLabel).toBe("경과 01:24.000");
    expect(result.secondaryLabel).toBe("관측 시각 미제공");
    expect(result.observedLabel).toBeNull();
    expect(result.receivedLabel).toBe("2026-10-07 00:00:00.007 KST");
  });

  it.each(["unknown", "observed_utc", "", "launcher_session_start"])(
    "does not reinterpret unknown or invalid server basis %s, regardless of magnitude or observed metadata",
    (time_basis) => {
      const result = eventTime({ timestamp_ms: 1_791_292_000_000, time_basis,
        observed_at_utc: "2026-10-06T12:00:00.999Z", received_at_utc: "2026-10-06T12:00:02.034+00:00" });
      expect(result.observedLabel).toBeNull();
      expect(result.elapsedLabel).toBeNull();
      expect(result.primaryLabel).toBe("timestamp 1791292000000 ms");
      expect(result.secondaryLabel).toBe("관측 시각 미제공");
      expect(result.receivedLabel).toBe("2026-10-06 21:00:02.034 KST");
    },
  );

  it("uses only the explicit epoch value for epoch observation, never receipt or unrelated metadata", () => {
    const epoch = eventTime({ timestamp_ms: 0, time_basis: "unix_epoch_ms",
      observed_at_utc: "2026-10-06T12:00:00Z", received_at_utc: "2026-10-06T12:00:01Z" });
    expect(epoch.primaryLabel).toBe("관측 1970-01-01 09:00:00.000 KST");
    expect(epoch.secondaryLabel).toBe("timestamp 0 ms");
    expect(epoch.receivedLabel).toBe("2026-10-06 21:00:01.000 KST");
  });

  it("rejects invalid UTC metadata instead of normalizing dates or using the host timezone", () => {
    for (const value of [undefined, null, "", "not-a-date", "2026-02-30T12:00:00Z",
      "2026-10-06T24:00:00Z", "2026-10-06T12:00:00", "2026-10-06T12:00:00+09:00"]) {
      expect(utcTimestamp(value)).toBeNull();
      const result = eventTime({ timestamp_ms: 10, time_basis: "session_relative",
        observed_at_utc: value, received_at_utc: value });
      expect(result.observedLabel).toBeNull();
      expect(result.receivedLabel).toBeNull();
      expect(result.primaryLabel).toBe("경과 00:00.010");
      expect(result.secondaryLabel).toBe("관측 시각 미제공");
    }
  });

  it("does not convert invalid numeric values or overflow into any clock", () => {
    for (const timestamp_ms of [NaN, Infinity, -1, 0.5, Number.MAX_SAFE_INTEGER + 1,
      true as unknown as number, "10" as unknown as number]) {
      for (const time_basis of ["session_relative", "unix_epoch_ms"]) {
        const result = eventTime({ timestamp_ms, time_basis, observed_at_utc: "2026-10-06T12:00:00Z" });
        expect(result.elapsedLabel).toBeNull();
        expect(result.observedLabel).toBeNull();
        expect(result.secondaryLabel).toBe("관측 시각 미제공");
      }
    }
    for (const timestamp_ms of [253_402_300_800_000, Number.MAX_SAFE_INTEGER]) {
      expect(eventTime({ timestamp_ms, time_basis: "unix_epoch_ms" }).observedLabel).toBeNull();
    }
  });

  it("does not mutate original payload or use sequence as a time", () => {
    const event = Object.freeze({ timestamp_ms: 246_771, time_basis: "unknown", sequence: 1_791_367_718_186,
      evidence: Object.freeze({ timestamp_basis: "launcher_session_start", session_start_unix_ms: 1_791_367_718_186 }),
      observed_at_utc: null, received_at_utc: null });
    expect(eventTime(event).primaryLabel).toBe("timestamp 246771 ms");
    expect(eventTime(event).observedLabel).toBeNull();
    expect(event.evidence.session_start_unix_ms).toBe(1_791_367_718_186);
  });
});
