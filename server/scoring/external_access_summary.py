"""external_access 원본 이력을 판정 없이 채널별로 요약한다.

중요:
- external_process와 module_integrity를 섞지 않는다.
- raw_score를 합산하지 않는다.
- module_integrity의 후속 NORMAL 0을 과거 양수 사건의 해소로 해석하지 않는다.
- ERROR/OFFLINE/measurement_valid=false는 정상 측정으로 세지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .storage import EXTERNAL_ACCESS_SUBMODULES, ExternalAccessEvent


@dataclass(frozen=True)
class ExternalAccessChannelSummary:
    session_id: str
    player_id: str
    submodule: str

    total_events: int
    available_events: int
    unavailable_events: int

    positive_events: int
    max_positive_raw_score: float | None
    has_positive_history: bool

    latest_event_id: str | None
    latest_raw_score: float | None
    latest_status: str | None
    latest_timestamp_ms: int | None
    latest_sequence: int | None

    latest_measurement_available: bool | None
    latest_is_normal_zero: bool


def _measurement_available(event: ExternalAccessEvent) -> bool:
    status = event.evidence.get("status")
    return (
        status not in ("ERROR", "OFFLINE")
        and event.evidence.get("measurement_valid") is not False
    )


def summarize_external_access_history(
    events: Iterable[ExternalAccessEvent],
    *,
    session_id: str,
    player_id: str,
    submodule: str,
) -> ExternalAccessChannelSummary:
    """external_access 하위 채널 하나의 원본 이력을 요약한다.

    이 함수는 calibration threshold, ACTIVE/INACTIVE, 최종 위험도를 계산하지 않는다.
    """

    if submodule not in EXTERNAL_ACCESS_SUBMODULES:
        raise ValueError("unsupported external_access submodule")

    items = tuple(events)

    for event in items:
        if event.module != "external_access":
            raise ValueError(
                "external_access summary accepts only external_access events"
            )
        if event.submodule != submodule:
            raise ValueError(
                "external_access history contains a different submodule"
            )
        if event.session_id != session_id:
            raise ValueError(
                "external_access history contains a different session_id"
            )
        if event.player_id != player_id:
            raise ValueError(
                "external_access history contains a different player_id"
            )

    if not items:
        return ExternalAccessChannelSummary(
            session_id=session_id,
            player_id=player_id,
            submodule=submodule,
            total_events=0,
            available_events=0,
            unavailable_events=0,
            positive_events=0,
            max_positive_raw_score=None,
            has_positive_history=False,
            latest_event_id=None,
            latest_raw_score=None,
            latest_status=None,
            latest_timestamp_ms=None,
            latest_sequence=None,
            latest_measurement_available=None,
            latest_is_normal_zero=False,
        )

    available = tuple(
        event
        for event in items
        if _measurement_available(event)
    )

    positives = tuple(
        event
        for event in available
        if float(event.raw_score) > 0
    )

    # storage 최신 상태와 같은 의미로 게임 timestamp 우선,
    # 동률이면 서버 sequence를 사용한다.
    latest = max(
        items,
        key=lambda event: (
            event.timestamp_ms,
            event.sequence,
        ),
    )

    latest_available = _measurement_available(latest)
    latest_status_value = latest.evidence.get("status")
    latest_status = (
        latest_status_value
        if isinstance(latest_status_value, str)
        else None
    )

    return ExternalAccessChannelSummary(
        session_id=session_id,
        player_id=player_id,
        submodule=submodule,
        total_events=len(items),
        available_events=len(available),
        unavailable_events=len(items) - len(available),
        positive_events=len(positives),
        max_positive_raw_score=(
            max(float(event.raw_score) for event in positives)
            if positives
            else None
        ),
        has_positive_history=bool(positives),
        latest_event_id=latest.event_id,
        latest_raw_score=float(latest.raw_score),
        latest_status=latest_status,
        latest_timestamp_ms=latest.timestamp_ms,
        latest_sequence=latest.sequence,
        latest_measurement_available=latest_available,
        latest_is_normal_zero=(
            latest_available
            and float(latest.raw_score) == 0
            and latest_status == "NORMAL"
        ),
    )
