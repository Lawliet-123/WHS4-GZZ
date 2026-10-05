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
from typing import Literal, Mapping

from .autopaint_history import AutoPaintHistorySummary
from .calibration import get_external_access_calibration
from .correlation import CorrelationCandidate
from .external_access_summary import ExternalAccessChannelSummary
from .history_summary import GodmodeHistorySummary
from .overlap import OverlapGroup, build_overlap_groups
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
    overlap_groups: tuple[OverlapGroup, ...]


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


def _resolve_external_access(
    summaries: Mapping[str, ExternalAccessChannelSummary] | None,
    *,
    session_id: str,
    player_id: str,
) -> AggregateSignalStatus | None:
    """두 external_access 채널을 각자 의미대로 해석한다."""

    if summaries is None:
        return None

    for submodule, summary in summaries.items():
        if summary.session_id != session_id:
            raise ValueError("external_access summary session_id does not match")
        if summary.player_id != player_id:
            raise ValueError("external_access summary player_id does not match")
        if summary.submodule != submodule:
            raise ValueError("external_access summary submodule does not match")

    process = summaries.get("external_process")
    integrity = summaries.get("module_integrity")

    process_calibration = get_external_access_calibration("external_process")
    integrity_calibration = get_external_access_calibration("module_integrity")

    if (
        process_calibration is None
        or integrity_calibration is None
        or not process_calibration.calibrated
        or not integrity_calibration.calibrated
    ):
        return "UNRESOLVED"

    process_active = (
        process is not None
        and process.latest_measurement_available is True
        and process.latest_raw_score is not None
        and process_calibration.meets_threshold(
            process.latest_raw_score
        ) is True
    )

    integrity_active = (
        integrity is not None
        and integrity.max_positive_raw_score is not None
        and integrity_calibration.meets_threshold(
            integrity.max_positive_raw_score
        ) is True
    )

    # 어느 한 채널이라도 확정된 양수 근거가 있으면
    # 다른 채널의 미완료 상태보다 ACTIVE 근거를 우선 보존한다.
    if process_active or integrity_active:
        return "ACTIVE"

    if process is None or integrity is None:
        return "UNRESOLVED"

    if process.total_events == 0 or integrity.total_events == 0:
        return "UNRESOLVED"

    # ACTIVE 근거가 없는 상황에서 최신 측정 자체가 실패했다면
    # 정상으로 강제하지 않는다.
    if (
        process.latest_measurement_available is False
        or integrity.latest_measurement_available is False
    ):
        return "UNAVAILABLE"

    if (
        process.latest_measurement_available is not True
        or integrity.latest_measurement_available is not True
    ):
        return "UNRESOLVED"

    return "INACTIVE"


def build_aggregate_evidence(
    risk_input: PlayerRiskInput,
    *,
    godmode_history: GodmodeHistorySummary | None = None,
    autopaint_history: AutoPaintHistorySummary | None = None,
    external_access_summaries: (
        Mapping[str, ExternalAccessChannelSummary] | None
    ) = None,
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

        if signal.module == "autopaint" and autopaint_history is not None:
            if autopaint_history.session_id != risk_input.session_id:
                raise ValueError(
                    "autopaint history session_id does not match risk input"
                )
            if autopaint_history.player_id != risk_input.player_id:
                raise ValueError(
                    "autopaint history player_id does not match risk input"
                )

            history_total_events = autopaint_history.total_events
            history_qualifying_events = autopaint_history.qualifying_events
            history_max_raw_score = autopaint_history.max_raw_score

            if autopaint_history.available_events > 0:
                history_resolved = True

            # 세션 중 threshold 이상으로 확정된 유효 snapshot이 있었다면
            # 종료 직전 최신 snapshot이 내려가도 세션 evidence는 ACTIVE로 유지한다.
            #
            # 현재 순간의 raw_score / threshold_met은 AggregateSignal에
            # 최신 snapshot 값 그대로 남아 있어 둘을 구분할 수 있다.
            if autopaint_history.positive_seen:
                status = "ACTIVE"

        if signal.module == "external_access":
            resolved_external_status = _resolve_external_access(
                external_access_summaries,
                session_id=risk_input.session_id,
                player_id=risk_input.player_id,
            )

            if resolved_external_status is not None:
                status = resolved_external_status
                history_resolved = status in ("ACTIVE", "INACTIVE")

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

    overlap_groups = build_overlap_groups(
        risk_input.correlation_candidates,
        active_modules=active,
    )

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
        overlap_groups=overlap_groups,
    )
