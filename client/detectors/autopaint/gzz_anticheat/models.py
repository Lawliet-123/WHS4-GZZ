from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CommonEvent:
    """The team-wide event contract. Do not add detector-private fields here."""

    session_id: str
    player_id: str
    module: str
    timestamp_ms: int
    evidence: dict[str, int]
    reasons: list[str]
    raw_score: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assessment_from_score(score: int) -> str:
    """Human-readable status for raw logs only; it is not part of CommonEvent."""

    if score >= 10:
        return "DETECTED"
    if score >= 5:
        return "SUSPICIOUS"
    if score > 0:
        return "OBSERVED"
    return "NORMAL"

