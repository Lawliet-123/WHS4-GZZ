"""Transparent, rule-based evidence scoring.

The score produced here is a review-priority signal.  It must not be presented
as the probability that a user cheated and it is intentionally insufficient for
an automatic permanent sanction.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import Iterable, Mapping

from .models import EvidenceEvent, ScoreSnapshot


@dataclass(frozen=True, slots=True)
class CategoryPolicy:
    """Explainable limits for one evidence category."""

    points_per_event: float
    category_cap: float
    window_seconds: float
    dedup_seconds: float
    max_events: int = 20

    def __post_init__(self) -> None:
        for name in (
            "points_per_event",
            "category_cap",
            "window_seconds",
            "dedup_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number")
            if not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.category_cap > 100:
            raise ValueError("category_cap cannot exceed 100")
        if (
            isinstance(self.max_events, bool)
            or not isinstance(self.max_events, int)
            or self.max_events < 1
        ):
            raise ValueError("max_events must be at least 1")


# Proposed PoC weights.  They are deliberately visible and should be calibrated
# against authorized normal/positive recordings before any operational use.
DEFAULT_POLICIES: dict[str, CategoryPolicy] = {
    # One high-quality, untrusted access to the exact game target reaches REVIEW
    # but cannot reach HIGH without corroboration or repetition.
    "memory_read": CategoryPolicy(45.0, 55.0, 600.0, 20.0, 5),
    "process_tamper": CategoryPolicy(40.0, 50.0, 600.0, 30.0, 4),
    "handle_duplicate": CategoryPolicy(24.0, 40.0, 600.0, 20.0, 5),
    "overlay": CategoryPolicy(12.0, 25.0, 300.0, 15.0, 8),
    "sensor_tamper": CategoryPolicy(45.0, 50.0, 900.0, 60.0, 3),
    # Optional behavioral corroboration must never outweigh direct telemetry.
    "behavioral_signal": CategoryPolicy(8.0, 16.0, 120.0, 8.0, 6),
}


class SuspicionEngine:
    """Accumulate evidence and produce bounded, explainable snapshots.

    Events outside their category time window do not contribute.  Repeated
    events with the same deduplication identity inside ``dedup_seconds`` count
    once, and each category is capped independently before the total is capped
    at 100.

    Observation confidence is calculated independently from live sensor
    coverage.  Callers may pass coverage to :meth:`score`, pass a direct 0..100
    confidence value, or maintain sensor state through :meth:`set_sensor_state`.
    """

    INSUFFICIENT = "INSUFFICIENT"
    LOW = "LOW"
    REVIEW = "REVIEW"
    HIGH = "HIGH"
    # Compatibility aliases; emitted snapshots use LOW/HIGH.
    MONITOR = LOW
    HIGH_REVIEW = HIGH

    def __init__(
        self,
        policies: Mapping[str, CategoryPolicy] | None = None,
        *,
        minimum_observation_confidence: float = 60.0,
        review_threshold: float = 35.0,
        high_review_threshold: float = 70.0,
        expected_sensors: Iterable[str] = (),
        sensor_ttl_seconds: float = 30.0,
    ) -> None:
        self.policies = dict(policies or DEFAULT_POLICIES)
        if not self.policies:
            raise ValueError("at least one category policy is required")
        self.minimum_observation_confidence = self._percent(
            "minimum_observation_confidence", minimum_observation_confidence
        )
        self.review_threshold = self._percent("review_threshold", review_threshold)
        self.high_review_threshold = self._percent(
            "high_review_threshold", high_review_threshold
        )
        if self.high_review_threshold < self.review_threshold:
            raise ValueError("high_review_threshold must be >= review_threshold")
        if (
            isinstance(sensor_ttl_seconds, bool)
            or not isinstance(sensor_ttl_seconds, (int, float))
            or not math.isfinite(float(sensor_ttl_seconds))
            or sensor_ttl_seconds <= 0
        ):
            raise ValueError("sensor_ttl_seconds must be positive and finite")
        self.sensor_ttl_seconds = float(sensor_ttl_seconds)
        self.expected_sensors = tuple(dict.fromkeys(str(item) for item in expected_sensors))
        if any(not item for item in self.expected_sensors):
            raise ValueError("expected sensor names must be non-empty")

        self._events: list[EvidenceEvent] = []
        self._sensor_state: dict[str, tuple[float, float]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _percent(name: str, value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a number")
        converted = float(value)
        if not math.isfinite(converted) or not 0.0 <= converted <= 100.0:
            raise ValueError(f"{name} must be between 0 and 100")
        return converted

    @staticmethod
    def _coverage_value(value: bool | int | float | None) -> float:
        if value is None:
            return 0.0
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise TypeError("sensor coverage values must be bool, number, or None")
        converted = float(value)
        # Accept both convenient 0..1 fractions and explicit 0..100 percentages.
        if 0.0 <= converted <= 1.0:
            return converted
        if 1.0 < converted <= 100.0:
            return converted / 100.0
        raise ValueError("sensor coverage values must be between 0..1 or 0..100")

    def add_event(self, event: EvidenceEvent) -> None:
        if not isinstance(event, EvidenceEvent):
            raise TypeError("event must be an EvidenceEvent")
        with self._lock:
            self._events.append(event)
            self._trim_active_buffers_locked()

    def add_events(self, events: Iterable[EvidenceEvent]) -> None:
        materialized = list(events)
        if any(not isinstance(event, EvidenceEvent) for event in materialized):
            raise TypeError("all events must be EvidenceEvent instances")
        with self._lock:
            self._events.extend(materialized)
            self._trim_active_buffers_locked()

    def _trim_active_buffers_locked(self) -> None:
        """Bound pre-score memory even if a sensor emits an event flood.

        Each supported category retains a generous multiple of the number of
        events that can actually contribute. Higher-quality evidence wins, with
        newer evidence breaking ties. The SQLite store remains the audit trail.
        """

        by_category: dict[str, list[EvidenceEvent]] = {}
        for event in self._events:
            by_category.setdefault(event.category, []).append(event)

        retained: list[EvidenceEvent] = []
        for category, category_events in by_category.items():
            policy = self.policies.get(category)
            limit = max(64, policy.max_events * 16) if policy is not None else 64
            if len(category_events) <= limit:
                retained.extend(category_events)
                continue
            points_per_event = policy.points_per_event if policy is not None else 0.0
            strongest = sorted(
                category_events,
                key=lambda event: (
                    points_per_event * event.strength * event.reliability,
                    event.timestamp,
                    event.event_id,
                ),
                reverse=True,
            )[:limit]
            retained.extend(strongest)
        self._events = sorted(
            retained,
            key=lambda event: (event.timestamp, event.event_id),
        )

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._sensor_state.clear()

    def events(self) -> tuple[EvidenceEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def set_sensor_state(
        self,
        sensor: str,
        coverage: bool | int | float | None,
        *,
        timestamp: float | None = None,
    ) -> None:
        """Record the most recent health/coverage value for a sensor."""

        if not isinstance(sensor, str) or not sensor.strip():
            raise ValueError("sensor must be a non-empty string")
        observed_at = time.time() if timestamp is None else float(timestamp)
        if not math.isfinite(observed_at) or observed_at < 0:
            raise ValueError("timestamp must be finite and non-negative")
        normalized = self._coverage_value(coverage)
        with self._lock:
            self._sensor_state[sensor.strip()] = (normalized, observed_at)

    @staticmethod
    def _event_dedup_identity(event: EvidenceEvent) -> str:
        if event.dedup_key:
            return event.dedup_key
        if event.subject_id:
            return f"subject:{event.subject_id}"
        for key in (
            "source_pid",
            "pid",
            "process_id",
            "source_path",
            "path",
            "hwnd",
        ):
            if key in event.details:
                return f"{key}:{event.details[key]}"
        return f"source:{event.source}"

    def _observation_confidence(
        self,
        *,
        now: float,
        sensor_coverage: Mapping[str, bool | int | float | None] | None,
        observation_confidence: float | None,
    ) -> float:
        if sensor_coverage is not None and observation_confidence is not None:
            raise ValueError(
                "provide sensor_coverage or observation_confidence, not both"
            )
        if observation_confidence is not None:
            return self._percent("observation_confidence", observation_confidence)
        if sensor_coverage is not None:
            if not sensor_coverage:
                return 0.0
            values = [self._coverage_value(value) for value in sensor_coverage.values()]
            return round(100.0 * sum(values) / len(values), 2)

        names = self.expected_sensors or tuple(self._sensor_state)
        if not names:
            return 0.0
        values: list[float] = []
        for name in names:
            state = self._sensor_state.get(name)
            if state is None:
                values.append(0.0)
                continue
            value, observed_at = state
            age = max(0.0, now - observed_at)
            values.append(value if age <= self.sensor_ttl_seconds else 0.0)
        return round(100.0 * sum(values) / len(values), 2)

    def score(
        self,
        *,
        now: float | None = None,
        sensor_coverage: Mapping[str, bool | int | float | None] | None = None,
        observation_confidence: float | None = None,
    ) -> ScoreSnapshot:
        evaluated_at = time.time() if now is None else float(now)
        if not math.isfinite(evaluated_at) or evaluated_at < 0:
            raise ValueError("now must be finite and non-negative")

        with self._lock:
            events = tuple(self._events)
            observed = self._observation_confidence(
                now=evaluated_at,
                sensor_coverage=sensor_coverage,
                observation_confidence=observation_confidence,
            )

        active_by_category: dict[str, list[EvidenceEvent]] = {
            category: [] for category in self.policies
        }
        expired_count = 0
        unsupported_count = 0
        window_starts: list[float] = []

        for event in events:
            policy = self.policies.get(event.category)
            if policy is None:
                unsupported_count += 1
                continue
            age = max(0.0, evaluated_at - event.timestamp)
            if age > policy.window_seconds:
                expired_count += 1
                continue
            active_by_category[event.category].append(event)
            window_starts.append(event.timestamp)

        category_scores: dict[str, float] = {}
        accepted_count = 0
        suppressed_count = 0
        active_count = sum(len(items) for items in active_by_category.values())

        for category, category_events in active_by_category.items():
            if not category_events:
                continue
            policy = self.policies[category]
            last_accepted: dict[str, float] = {}
            accepted: list[EvidenceEvent] = []
            for event in sorted(category_events, key=lambda item: (item.timestamp, item.event_id)):
                identity = self._event_dedup_identity(event)
                previous = last_accepted.get(identity)
                if previous is not None and event.timestamp - previous < policy.dedup_seconds:
                    suppressed_count += 1
                    continue
                last_accepted[identity] = event.timestamp
                accepted.append(event)

            # When a noisy category still has many distinct identities, retain the
            # strongest observations and enforce the explicit max-event limit.
            contributions = sorted(
                (
                    policy.points_per_event * event.strength * event.reliability
                    for event in accepted
                ),
                reverse=True,
            )
            if len(contributions) > policy.max_events:
                suppressed_count += len(contributions) - policy.max_events
                contributions = contributions[: policy.max_events]
            accepted_count += len(contributions)
            category_scores[category] = round(
                min(policy.category_cap, sum(contributions)), 2
            )

        suspicion = round(min(100.0, sum(category_scores.values())), 2)
        if observed < self.minimum_observation_confidence:
            status = self.INSUFFICIENT
        elif suspicion >= self.high_review_threshold:
            status = self.HIGH
        elif suspicion >= self.review_threshold:
            status = self.REVIEW
        else:
            status = self.LOW

        reasons = [
            f"{category}: {points:.2f}/{self.policies[category].category_cap:.2f} points"
            for category, points in sorted(
                category_scores.items(), key=lambda item: (-item[1], item[0])
            )
        ]
        if suppressed_count:
            reasons.append(f"{suppressed_count} duplicate/capped event(s) suppressed")
        if expired_count:
            reasons.append(f"{expired_count} expired event(s) excluded")
        if unsupported_count:
            reasons.append(f"{unsupported_count} unsupported category event(s) excluded")
        if status == self.INSUFFICIENT:
            reasons.append(
                "observation confidence is below the configured review threshold"
            )

        # Keep the in-memory working set bounded. SQLite remains the forensic
        # history; the scoring engine only needs events that can still contribute.
        with self._lock:
            self._events = [
                event
                for event in self._events
                if (
                    (policy := self.policies.get(event.category)) is not None
                    and max(0.0, evaluated_at - event.timestamp)
                    <= policy.window_seconds
                )
            ]

        return ScoreSnapshot(
            evaluated_at=evaluated_at,
            suspicion=suspicion,
            observation_confidence=observed,
            status=status,
            active_event_count=active_count,
            accepted_event_count=accepted_count,
            suppressed_event_count=suppressed_count,
            expired_event_count=expired_count,
            category_scores=category_scores,
            reasons=tuple(reasons),
            window_start=min(window_starts) if window_starts else None,
        )

    def snapshot(self, **kwargs: object) -> ScoreSnapshot:
        """Alias used by UI/controller integrations."""

        return self.score(**kwargs)  # type: ignore[arg-type]
