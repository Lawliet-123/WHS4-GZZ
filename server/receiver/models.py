"""Validation models for the team-wide detection result format."""

from __future__ import annotations

from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DetectionResult(BaseModel):
    """One detector result accepted by ``POST /api/detection``.

    Extra fields are deliberately preserved. Some detectors attach useful
    metadata such as ``status`` or ``window_id`` in addition to the seven
    common fields, and the receiver must not discard that evidence.
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    session_id: str = Field(min_length=1, max_length=128)
    player_id: str = Field(min_length=1, max_length=128)
    module: str = Field(min_length=1, max_length=128)
    timestamp_ms: int = Field(ge=0)
    evidence: dict[str, Any]
    reasons: list[str]
    raw_score: float = Field(ge=0)

    @field_validator("timestamp_ms", mode="before")
    @classmethod
    def timestamp_must_be_an_integer(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("timestamp_ms must be an integer")
        return value

    @field_validator("raw_score", mode="before")
    @classmethod
    def score_must_be_a_finite_number(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("raw_score must be a number")
        try:
            score = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("raw_score must be a number") from exc
        if not isfinite(score):
            raise ValueError("raw_score must be finite")
        return value

    @field_validator("reasons")
    @classmethod
    def reasons_must_be_text(cls, reasons: list[str]) -> list[str]:
        if any(not reason.strip() for reason in reasons):
            raise ValueError("reasons must not contain empty text")
        return reasons

    def as_payload(self) -> dict[str, Any]:
        """Return the validated event, including permitted extra evidence."""
        return self.model_dump(mode="json")
