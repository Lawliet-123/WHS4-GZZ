"""event-history 기반 모듈의 읽기 전용 요약.

현재는 Godmode event_delta history만 지원한다.

중요:
- 사건 raw_score를 합산하지 않는다.
- latest_state 한 건으로 과거 사건을 대체하지 않는다.
- threshold를 넘은 사건의 개수/최댓값/시간 범위만 요약한다.
- 최종 Aggregate Risk 또는 Verdict를 계산하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .calibration import require_calibration
from .storage import DeltaEvent


@dataclass(frozen=True)
class GodmodeHistorySummary:
    session_id: str
    player_id: str

    calibration_version: str
    threshold: float

    total_events: int
    qualifying_events: int

    max_raw_score: float | None

    first_timestamp_ms: int | None
    last_timestamp_ms: int | None

    first_sequence: int | None
    last_sequence: int | None

    reasons: tuple[str, ...]


def summarize_godmode_history(
    events: Iterable[DeltaEvent],
    *,
    session_id: str,
    player_id: str,
) -> GodmodeHistorySummary:
    """Godmode event_delta history를 합산 없이 요약한다."""

    calibration = require_calibration("godmode")

    if calibration.mode != "event_threshold":
        raise RuntimeError(
            "godmode calibration must use event_threshold mode"
        )

    assert calibration.threshold is not None
    threshold = float(calibration.threshold)

    items = tuple(events)

    for event in items:
        if event.module != "godmode":
            raise ValueError(
                "godmode history summary accepts only godmode events"
            )

        if event.session_id != session_id:
            raise ValueError(
                "godmode history contains a different session_id"
            )

        if event.player_id != player_id:
            raise ValueError(
                "godmode history contains a different player_id"
            )

    if not items:
        return GodmodeHistorySummary(
            session_id=session_id,
            player_id=player_id,
            calibration_version=calibration.version,
            threshold=threshold,
            total_events=0,
            qualifying_events=0,
            max_raw_score=None,
            first_timestamp_ms=None,
            last_timestamp_ms=None,
            first_sequence=None,
            last_sequence=None,
            reasons=(),
        )

    qualifying_events = sum(
        1
        for event in items
        if calibration.meets_threshold(event.raw_score) is True
    )

    reasons = tuple(
        sorted(
            {
                reason
                for event in items
                for reason in event.reasons
            }
        )
    )

    return GodmodeHistorySummary(
        session_id=session_id,
        player_id=player_id,
        calibration_version=calibration.version,
        threshold=threshold,
        total_events=len(items),
        qualifying_events=qualifying_events,
        max_raw_score=max(float(event.raw_score) for event in items),
        first_timestamp_ms=min(event.timestamp_ms for event in items),
        last_timestamp_ms=max(event.timestamp_ms for event in items),
        first_sequence=min(event.sequence for event in items),
        last_sequence=max(event.sequence for event in items),
        reasons=reasons,
    )
