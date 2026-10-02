"""Source-agnostic contracts between LocalGuard sensors and detectors."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4


SENSOR_SCHEMA_VERSION = "meccha.sensor-event.v1"
DETECTION_SCHEMA_VERSION = "meccha.detector-decision.v1"
TEAM_EVENT_SCHEMA_VERSION = "meccha.team-detection-event.v1"
DECISION_STATUSES = frozenset({"NORMAL", "REVIEW", "HIGH", "INSUFFICIENT"})
SENSOR_BATCH_STATUSES = frozenset(
    {"online", "waiting", "error", "unavailable", "disabled"}
)


def _non_empty(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _timestamp_ms(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timestamp_ms must be a number")
    converted = float(value)
    if not math.isfinite(converted) or converted < 0:
        raise ValueError("timestamp_ms must be finite and non-negative")
    return int(round(converted))


def _json_object(name: str, value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"{name} keys must be strings")
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
        result = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must contain JSON-serializable values") from exc
    if not isinstance(result, dict):
        raise TypeError(f"{name} must encode to an object")
    return result


@dataclass(slots=True)
class SensorEvent:
    """A factual observation emitted by one sensor.

    Sensors may use Windows, Sysmon, or game-specific data sources. Detectors
    only consume this normalized value and therefore do not need those APIs.
    """

    session_id: str
    sensor_id: str
    event_type: str
    subject_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    event_id: str = field(default_factory=lambda: uuid4().hex)
    source_module: str = "local_guard"
    sequence: int | None = None
    schema_version: str = SENSOR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.session_id = _non_empty("session_id", self.session_id)
        self.sensor_id = _non_empty("sensor_id", self.sensor_id)
        self.event_type = _non_empty("event_type", self.event_type)
        self.subject_id = _non_empty("subject_id", self.subject_id)
        self.event_id = _non_empty("event_id", self.event_id)
        self.source_module = _non_empty("source_module", self.source_module)
        self.schema_version = _non_empty("schema_version", self.schema_version)
        self.timestamp_ms = _timestamp_ms(self.timestamp_ms)
        if self.sequence is not None:
            if (
                isinstance(self.sequence, bool)
                or not isinstance(self.sequence, int)
                or self.sequence < 0
            ):
                raise ValueError("sequence must be a non-negative integer or None")
        self.payload = _json_object("payload", self.payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": "sensor_event",
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "timestamp_ms": self.timestamp_ms,
            "sequence": self.sequence,
            "source_module": self.source_module,
            "sensor_id": self.sensor_id,
            "event_type": self.event_type,
            "subject_id": self.subject_id,
            "payload": _json_object("payload", self.payload),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SensorEvent":
        data = dict(value)
        return cls(
            session_id=data["session_id"],
            sensor_id=data["sensor_id"],
            event_type=data["event_type"],
            subject_id=data["subject_id"],
            payload=data.get("payload", {}),
            timestamp_ms=data.get("timestamp_ms", int(time.time() * 1000)),
            event_id=data.get("event_id", uuid4().hex),
            source_module=data.get("source_module", "local_guard"),
            sequence=data.get("sequence"),
            schema_version=data.get("schema_version", SENSOR_SCHEMA_VERSION),
        )


@dataclass(slots=True)
class DetectorDecision:
    """An explainable detector result referencing normalized sensor events."""

    session_id: str
    detector_id: str
    subject_id: str
    status: str
    score: float
    reasons: tuple[str, ...] = ()
    evidence_event_ids: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    decision_id: str = field(default_factory=lambda: uuid4().hex)
    schema_version: str = DETECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.session_id = _non_empty("session_id", self.session_id)
        self.detector_id = _non_empty("detector_id", self.detector_id)
        self.subject_id = _non_empty("subject_id", self.subject_id)
        self.decision_id = _non_empty("decision_id", self.decision_id)
        self.schema_version = _non_empty("schema_version", self.schema_version)
        self.status = _non_empty("status", self.status).upper()
        if self.status not in DECISION_STATUSES:
            raise ValueError(f"unsupported detector status: {self.status}")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise TypeError("score must be a number")
        self.score = float(self.score)
        if not math.isfinite(self.score) or not 0.0 <= self.score <= 100.0:
            raise ValueError("score must be between 0 and 100")
        self.timestamp_ms = _timestamp_ms(self.timestamp_ms)
        self.reasons = tuple(str(item) for item in self.reasons)
        self.evidence_event_ids = tuple(
            _non_empty("evidence_event_id", item) for item in self.evidence_event_ids
        )
        self.details = _json_object("details", self.details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": "detector_decision",
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "session_id": self.session_id,
            "timestamp_ms": self.timestamp_ms,
            "detector_id": self.detector_id,
            "subject_id": self.subject_id,
            "status": self.status,
            "score": self.score,
            "reasons": list(self.reasons),
            "evidence_event_ids": list(self.evidence_event_ids),
            "details": _json_object("details", self.details),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DetectorDecision":
        data = dict(value)
        return cls(
            session_id=data["session_id"],
            detector_id=data["detector_id"],
            subject_id=data["subject_id"],
            status=data["status"],
            score=data["score"],
            reasons=tuple(data.get("reasons", ())),
            evidence_event_ids=tuple(data.get("evidence_event_ids", ())),
            details=data.get("details", {}),
            timestamp_ms=data.get("timestamp_ms", int(time.time() * 1000)),
            decision_id=data.get("decision_id", uuid4().hex),
            schema_version=data.get("schema_version", DETECTION_SCHEMA_VERSION),
        )


@dataclass(slots=True)
class TeamDetectionEvent:
    """The team-wide detector result contract supplied on 2026-09-19.

    ``timestamp_ms`` is elapsed time from the beginning of the named test
    session, not Unix epoch milliseconds.  Raw sensor records keep their own
    source clocks under ``raw/``.
    """

    session_id: str
    player_id: str
    module: str
    timestamp_ms: int
    evidence: dict[str, Any]
    reasons: tuple[str, ...]
    raw_score: int
    schema_version: str = TEAM_EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.session_id = _non_empty("session_id", self.session_id)
        self.player_id = _non_empty("player_id", self.player_id)
        self.module = _non_empty("module", self.module).lower()
        self.schema_version = _non_empty("schema_version", self.schema_version)
        self.timestamp_ms = _timestamp_ms(self.timestamp_ms)
        self.evidence = _json_object("evidence", self.evidence)
        self.reasons = tuple(_non_empty("reason", item) for item in self.reasons)
        if (
            isinstance(self.raw_score, bool)
            or not isinstance(self.raw_score, int)
            or self.raw_score < 0
        ):
            raise ValueError("raw_score must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        # Keep the public payload exactly to the fields supplied by the team.
        # schema_version remains an in-process contract and belongs in manifest.
        return {
            "session_id": self.session_id,
            "player_id": self.player_id,
            "module": self.module,
            "timestamp_ms": self.timestamp_ms,
            "evidence": _json_object("evidence", self.evidence),
            "reasons": list(self.reasons),
            "raw_score": self.raw_score,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TeamDetectionEvent":
        data = dict(value)
        return cls(
            session_id=data["session_id"],
            player_id=data["player_id"],
            module=data["module"],
            timestamp_ms=data["timestamp_ms"],
            evidence=data.get("evidence", {}),
            reasons=tuple(data.get("reasons", ())),
            raw_score=data["raw_score"],
            schema_version=data.get("schema_version", TEAM_EVENT_SCHEMA_VERSION),
        )


@dataclass(slots=True)
class SensorBatch:
    """One sensor poll result, including health separately from observations."""

    sensor_id: str
    status: str
    events: tuple[SensorEvent, ...] = ()
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    observed_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def __post_init__(self) -> None:
        self.sensor_id = _non_empty("sensor_id", self.sensor_id)
        self.status = _non_empty("status", self.status).lower()
        if self.status not in SENSOR_BATCH_STATUSES:
            raise ValueError(f"unsupported sensor batch status: {self.status}")
        self.events = tuple(self.events)
        if any(not isinstance(event, SensorEvent) for event in self.events):
            raise TypeError("events must contain only SensorEvent values")
        if not isinstance(self.message, str):
            raise TypeError("message must be a string")
        self.details = _json_object("details", self.details)
        self.observed_at_ms = _timestamp_ms(self.observed_at_ms)


__all__ = [
    "DECISION_STATUSES",
    "DETECTION_SCHEMA_VERSION",
    "DetectorDecision",
    "SENSOR_BATCH_STATUSES",
    "SENSOR_SCHEMA_VERSION",
    "SensorBatch",
    "SensorEvent",
    "TEAM_EVENT_SCHEMA_VERSION",
    "TeamDetectionEvent",
]
