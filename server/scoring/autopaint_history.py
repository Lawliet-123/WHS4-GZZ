"""AutoPaint snapshot history를 세션 단위로 요약한다.

현재 순간의 상태와 세션 중 확정된 positive 이력을 분리한다.
raw_score를 합산하지 않으며, calibration threshold는 기존 값을 그대로 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .calibration import require_calibration
from .storage import SnapshotEvent


@dataclass(frozen=True)
class AutoPaintHistorySummary:
    session_id: str
    player_id: str

    calibration_version: str
    threshold: float

    total_events: int
    available_events: int
    unavailable_events: int
    qualifying_events: int

    max_raw_score: float | None
    latest_raw_score: float | None
    latest_threshold_met: bool | None

    positive_seen: bool

    first_timestamp_ms: int | None
    last_timestamp_ms: int | None
    first_sequence: int | None
    last_sequence: int | None


def _measurement_available(event: SnapshotEvent) -> bool:
    evidence = event.evidence
    status = evidence.get("status")

    if status in ("ERROR", "OFFLINE"):
        return False

    if evidence.get("measurement_valid") is False:
        return False

    # AutoPaint 정책과 동일하게 두 채널이 모두 명시적으로 무효면
    # raw_score가 무엇이든 positive history로 사용하지 않는다.
    if (
        evidence.get("integrity_valid") == 0
        and evidence.get("behavior_valid") == 0
    ):
        return False

    return True


def summarize_autopaint_history(
    events: Iterable[SnapshotEvent],
    *,
    session_id: str,
    player_id: str,
) -> AutoPaintHistorySummary:
    """AutoPaint snapshot 전체를 threshold 합산 없이 요약한다."""

    calibration = require_calibration("autopaint")

    if calibration.mode != "threshold":
        raise RuntimeError("autopaint calibration must use threshold mode")

    assert calibration.threshold is not None
    threshold = float(calibration.threshold)

    items = tuple(events)

    for event in items:
        if event.module != "autopaint":
            raise ValueError(
                "autopaint history summary accepts only autopaint events"
            )
        if event.session_id != session_id:
            raise ValueError(
                "autopaint history contains a different session_id"
            )
        if event.player_id != player_id:
            raise ValueError(
                "autopaint history contains a different player_id"
            )

    if not items:
        return AutoPaintHistorySummary(
            session_id=session_id,
            player_id=player_id,
            calibration_version=calibration.version,
            threshold=threshold,
            total_events=0,
            available_events=0,
            unavailable_events=0,
            qualifying_events=0,
            max_raw_score=None,
            latest_raw_score=None,
            latest_threshold_met=None,
            positive_seen=False,
            first_timestamp_ms=None,
            last_timestamp_ms=None,
            first_sequence=None,
            last_sequence=None,
        )

    available = tuple(
        event for event in items if _measurement_available(event)
    )

    qualifying = tuple(
        event
        for event in available
        if calibration.meets_threshold(event.raw_score) is True
    )

    # latest_state와 같은 의미로 game timestamp 우선,
    # 동률이면 서버 sequence를 사용한다.
    latest = max(
        items,
        key=lambda event: (
            event.timestamp_ms,
            event.sequence,
        ),
    )

    latest_available = _measurement_available(latest)

    return AutoPaintHistorySummary(
        session_id=session_id,
        player_id=player_id,
        calibration_version=calibration.version,
        threshold=threshold,
        total_events=len(items),
        available_events=len(available),
        unavailable_events=len(items) - len(available),
        qualifying_events=len(qualifying),
        max_raw_score=(
            max(float(event.raw_score) for event in available)
            if available
            else None
        ),
        latest_raw_score=float(latest.raw_score),
        latest_threshold_met=(
            calibration.meets_threshold(latest.raw_score)
            if latest_available
            else None
        ),
        positive_seen=bool(qualifying),
        first_timestamp_ms=min(event.timestamp_ms for event in items),
        last_timestamp_ms=max(event.timestamp_ms for event in items),
        first_sequence=min(event.sequence for event in items),
        last_sequence=max(event.sequence for event in items),
    )
