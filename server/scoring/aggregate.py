"""Aggregate Risk 계산 직전의 증거 분류 계층.

이 단계에서는 아직 가중치, 합산 점수, 확률, Final Verdict를 만들지 않는다.

각 RiskSignalInput을 다음 상태 중 하나로 분류한다.

- ACTIVE: calibrated threshold를 충족한 현재 snapshot
- INACTIVE: calibrated threshold를 충족하지 않은 현재 snapshot
- ADVISORY: 직접 threshold 판정에 사용하지 않는 보조 증거
- DEFERRED: event/window history 등 추가 이력이 필요한 신호
- UNRESOLVED: calibration/policy가 아직 확정되지 않은 신호
- UNAVAILABLE: ERROR/OFFLINE 등 측정 불가 신호

CorrelationCandidate는 그대로 보존하고 실제 중복 감산은 overlap 계약 확정 후 수행한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .correlation import CorrelationCandidate
from .history_summary import GodmodeHistorySummary
from .risk_input import PlayerRiskInput, RiskSignalInput


AggregateSignalStatus = Literal[
    "ACTIVE",
    "INACTIVE",
    "ADVISORY",
    "DEFERRED",
    "UNRESOLVED",
    "UNAVAILABLE",
]


@dataclass(frozen=True)
class AggregateSignal:
    module: str
    event_id: str
    status: AggregateSignalStatus
    raw_score: float
    calibration_version: str | None
    calibration_mode: str | None
    calibration_threshold: float | None
    threshold_met: bool | None
    overlap_tags: tuple[str, ...]
    entity_key: str | None

    history_resolved: bool = False
    history_total_events: int | None = None
    history_qualifying_events: int | None = None
    history_max_raw_score: float | None = None


@dataclass(frozen=True)
class AggregateEvidence:
    """최종 risk 계산 직전의 플레이어별 분류 결과."""

    session_id: str
    player_id: str
    signals: tuple[AggregateSignal, ...]

    active_modules: tuple[str, ...]
    inactive_modules: tuple[str, ...]
    advisory_modules: tuple[str, ...]
    deferred_modules: tuple[str, ...]
    unresolved_modules: tuple[str, ...]
    unavailable_modules: tuple[str, ...]

    correlation_candidates: tuple[CorrelationCandidate, ...]


def _classify(
    signal: RiskSignalInput,
    risk_input: PlayerRiskInput,
) -> AggregateSignalStatus:
    if not signal.measurement_available:
        return "UNAVAILABLE"

    if signal.module in risk_input.unresolved_policy_modules:
        return "UNRESOLVED"

    # event_delta/window_history는 최신 한 건만으로 Aggregate Risk에 넣지 않는다.
    if signal.requires_event_history:
        return "DEFERRED"

    if signal.calibration_mode == "advisory":
        return "ADVISORY"

    if signal.threshold_met is True:
        return "ACTIVE"

    if signal.threshold_met is False:
        return "INACTIVE"

    # calibration은 존재하지만 아직 이 층에서 직접 판정할 수 없는 경우.
    return "DEFERRED"


def build_aggregate_evidence(
    risk_input: PlayerRiskInput,
    *,
    godmode_history: GodmodeHistorySummary | None = None,
) -> AggregateEvidence:
    """PlayerRiskInput을 합산 전 증거 분류 결과로 변환한다.

    Godmode event_delta는 history summary가 제공된 경우에만
    DEFERRED 상태를 ACTIVE/INACTIVE로 해소한다.

    qualifying 사건 개수나 raw_score를 합산해 risk 점수로 바꾸지는 않는다.
    """

    if not isinstance(risk_input, PlayerRiskInput):
        raise TypeError("risk_input must be PlayerRiskInput")

    signals: list[AggregateSignal] = []

    active: list[str] = []
    inactive: list[str] = []
    advisory: list[str] = []
    deferred: list[str] = []
    unresolved: list[str] = []
    unavailable: list[str] = []

    buckets = {
        "ACTIVE": active,
        "INACTIVE": inactive,
        "ADVISORY": advisory,
        "DEFERRED": deferred,
        "UNRESOLVED": unresolved,
        "UNAVAILABLE": unavailable,
    }

    for signal in risk_input.signals:
        status = _classify(signal, risk_input)

        history_resolved = False
        history_total_events = None
        history_qualifying_events = None
        history_max_raw_score = None

        if signal.module == "godmode" and signal.requires_event_history:
            if godmode_history is not None:
                if godmode_history.session_id != risk_input.session_id:
                    raise ValueError(
                        "godmode history session_id does not match risk input"
                    )
                if godmode_history.player_id != risk_input.player_id:
                    raise ValueError(
                        "godmode history player_id does not match risk input"
                    )

                history_total_events = godmode_history.total_events
                history_qualifying_events = (
                    godmode_history.qualifying_events
                )
                history_max_raw_score = godmode_history.max_raw_score

                if godmode_history.total_events > 0:
                    history_resolved = True

                    if godmode_history.qualifying_events > 0:
                        status = "ACTIVE"
                    else:
                        status = "INACTIVE"

        signals.append(
            AggregateSignal(
                module=signal.module,
                event_id=signal.event_id,
                status=status,
                raw_score=signal.raw_score,
                calibration_version=signal.calibration_version,
                calibration_mode=signal.calibration_mode,
                calibration_threshold=signal.calibration_threshold,
                threshold_met=signal.threshold_met,
                overlap_tags=signal.overlap_tags,
                entity_key=signal.entity_key,
                history_resolved=history_resolved,
                history_total_events=history_total_events,
                history_qualifying_events=history_qualifying_events,
                history_max_raw_score=history_max_raw_score,
            )
        )

        buckets[status].append(signal.module)

    return AggregateEvidence(
        session_id=risk_input.session_id,
        player_id=risk_input.player_id,
        signals=tuple(signals),
        active_modules=tuple(active),
        inactive_modules=tuple(inactive),
        advisory_modules=tuple(advisory),
        deferred_modules=tuple(deferred),
        unresolved_modules=tuple(unresolved),
        unavailable_modules=tuple(unavailable),
        correlation_candidates=risk_input.correlation_candidates,
    )
