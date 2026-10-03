"""Data contracts shared by the Anti-ESP monitor.

The models deliberately contain no platform-specific code.  All timestamps are
Unix seconds and all mappings returned by ``to_dict`` can be passed directly to
``json.dumps``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import time
from typing import Any, Mapping
from uuid import uuid4


def _finite_number(name: str, value: Any, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    converted = float(value)
    if not math.isfinite(converted) or not minimum <= converted <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return converted


def _json_object(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and defensively copy a JSON object.

    ``json.dumps`` normally coerces non-string dictionary keys.  Rejecting them
    here prevents a stored evidence record from silently changing on round-trip.
    ``allow_nan=False`` also keeps NaN and infinities out of evidence files.
    """

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("details must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise TypeError("details keys must be strings")
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise TypeError("details must contain only JSON-serializable values") from exc
    if not isinstance(copied, dict):  # defensive; Mapping above should ensure this
        raise TypeError("details must encode to a JSON object")
    return copied


@dataclass(slots=True)
class EvidenceEvent:
    """A single, factual observation produced by a monitor sensor.

    ``strength`` describes how strongly the observed condition was present.
    ``reliability`` describes the quality of that particular observation.  Both
    use the 0..1 range.  ``confidence`` is a backwards-compatible constructor
    and attribute alias for ``reliability``; the two are normalized to the same
    value.  Neither value is a probability that a user is cheating.
    """

    category: str
    strength: float = 1.0
    reliability: float = 1.0
    source: str = "unknown"
    timestamp: float = field(default_factory=time.time)
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    dedup_key: str | None = None
    event_id: str = field(default_factory=lambda: uuid4().hex)
    session_id: str = "default"
    subject_id: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.category, str) or not self.category.strip():
            raise ValueError("category must be a non-empty string")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if self.subject_id is not None and not isinstance(self.subject_id, str):
            raise TypeError("subject_id must be a string or None")
        if self.dedup_key is not None and not isinstance(self.dedup_key, str):
            raise TypeError("dedup_key must be a string or None")

        self.category = self.category.strip()
        self.source = self.source.strip()
        self.event_id = self.event_id.strip()
        self.session_id = self.session_id.strip()
        self.strength = _finite_number("strength", self.strength, 0.0, 1.0)
        self.reliability = _finite_number("reliability", self.reliability, 0.0, 1.0)
        if self.confidence is not None:
            alias = _finite_number("confidence", self.confidence, 0.0, 1.0)
            if self.reliability != 1.0 and not math.isclose(self.reliability, alias):
                raise ValueError("reliability and confidence disagree")
            self.reliability = alias
        self.confidence = self.reliability
        self.timestamp = _finite_number("timestamp", self.timestamp, 0.0, float("1e20"))
        self.details = _json_object(self.details)

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, JSON-serializable representation."""

        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "subject_id": self.subject_id,
            "category": self.category,
            "source": self.source,
            "strength": self.strength,
            "reliability": self.reliability,
            "confidence": self.reliability,
            "reason": self.reason,
            "dedup_key": self.dedup_key,
            "details": _json_object(self.details),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceEvent":
        data = dict(payload)
        if "reliability" not in data and "confidence" in data:
            data["reliability"] = data["confidence"]
        return cls(
            category=data["category"],
            strength=data.get("strength", 1.0),
            reliability=data.get("reliability", 1.0),
            source=data.get("source", "unknown"),
            timestamp=data.get("timestamp", time.time()),
            reason=data.get("reason", ""),
            details=data.get("details", {}),
            dedup_key=data.get("dedup_key"),
            event_id=data.get("event_id", uuid4().hex),
            session_id=data.get("session_id", "default"),
            subject_id=data.get("subject_id"),
            confidence=data.get("confidence"),
        )

    @classmethod
    def from_json(cls, payload: str) -> "EvidenceEvent":
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise TypeError("evidence JSON must contain an object")
        return cls.from_dict(decoded)


@dataclass(slots=True)
class ScoreSnapshot:
    """An explainable score at one point in time.

    ``suspicion`` and ``observation_confidence`` are independent 0..100 scales.
    Suspicion is a triage score, not a probability or a guilt determination.
    """

    evaluated_at: float
    suspicion: float
    observation_confidence: float
    status: str
    active_event_count: int = 0
    accepted_event_count: int = 0
    suppressed_event_count: int = 0
    expired_event_count: int = 0
    category_scores: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    window_start: float | None = None

    def __post_init__(self) -> None:
        self.evaluated_at = _finite_number(
            "evaluated_at", self.evaluated_at, 0.0, float("1e20")
        )
        self.suspicion = _finite_number("suspicion", self.suspicion, 0.0, 100.0)
        self.observation_confidence = _finite_number(
            "observation_confidence", self.observation_confidence, 0.0, 100.0
        )
        if not isinstance(self.status, str) or not self.status:
            raise ValueError("status must be a non-empty string")
        for name in (
            "active_event_count",
            "accepted_event_count",
            "suppressed_event_count",
            "expired_event_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        cleaned_scores: dict[str, float] = {}
        for category, score in self.category_scores.items():
            if not isinstance(category, str):
                raise TypeError("category score keys must be strings")
            cleaned_scores[category] = _finite_number(
                f"category_scores[{category}]", score, 0.0, 100.0
            )
        self.category_scores = cleaned_scores
        self.reasons = tuple(str(reason) for reason in self.reasons)
        if self.window_start is not None:
            self.window_start = _finite_number(
                "window_start", self.window_start, 0.0, float("1e20")
            )

    @property
    def suspicion_score(self) -> float:
        """Dashboard-friendly alias for :attr:`suspicion`."""

        return self.suspicion

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated_at": self.evaluated_at,
            "suspicion": self.suspicion,
            "suspicion_score": self.suspicion,
            "observation_confidence": self.observation_confidence,
            "status": self.status,
            "active_event_count": self.active_event_count,
            "accepted_event_count": self.accepted_event_count,
            "suppressed_event_count": self.suppressed_event_count,
            "expired_event_count": self.expired_event_count,
            "category_scores": dict(self.category_scores),
            "reasons": list(self.reasons),
            "window_start": self.window_start,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScoreSnapshot":
        data = dict(payload)
        suspicion = data.get("suspicion", data.get("suspicion_score"))
        if suspicion is None:
            raise KeyError("suspicion")
        return cls(
            evaluated_at=data["evaluated_at"],
            suspicion=suspicion,
            observation_confidence=data["observation_confidence"],
            status=data["status"],
            active_event_count=data.get("active_event_count", 0),
            accepted_event_count=data.get("accepted_event_count", 0),
            suppressed_event_count=data.get("suppressed_event_count", 0),
            expired_event_count=data.get("expired_event_count", 0),
            category_scores=data.get("category_scores", {}),
            reasons=tuple(data.get("reasons", ())),
            window_start=data.get("window_start"),
        )

    @classmethod
    def from_json(cls, payload: str) -> "ScoreSnapshot":
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise TypeError("snapshot JSON must contain an object")
        return cls.from_dict(decoded)
