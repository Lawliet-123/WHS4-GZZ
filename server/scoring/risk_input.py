"""최종 risk 계산 전에 Policy 결과와 Replay calibration을 안전하게 정리한다.

이 계층은 아직 detector 가중치, 점수 합산, 최종 verdict를 계산하지 않는다.

역할:
- Policy가 보존한 detector별 raw 의미를 유지한다.
- ERROR/OFFLINE을 정상 0점과 구분한다.
- event_delta/window_history/entity-scope 제약을 보존한다.
- Replay calibration 설정을 각 signal에 연결한다.
- calibration이 완료된 모듈과 pending 모듈을 명확히 구분한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .calibration import ModuleCalibration, resolve_calibration
from .correlation import CorrelationCandidate
from .player_snapshot import PlayerPolicySnapshot


_MEASUREMENT_UNAVAILABLE_STATES = frozenset({
    "MEASUREMENT_UNAVAILABLE",
})

# calibration이 있어도 그대로 risk에 넣으면 안 되는 Policy 상태.
_HARD_UNRESOLVED_POLICY_STATES = frozenset({
    "UNKNOWN_MODULE",
    "AWAITING_DETECTOR",
    "OUT_OF_AUDITED_RANGE",
})


# SelfDefense는 치트 탐지기가 아니라 안티치트 구성요소의 운영 상태를 보고한다.
# 원본 Event는 Storage/Dashboard에 그대로 남기되 cheat-risk 계산에서는 제외한다.
_OPERATIONAL_ONLY_MODULES = frozenset({
    "selfdefense",
})


@dataclass(frozen=True)
class RiskSignalInput:
    """모듈 1개의 Policy + calibration 결과."""

    module: str
    event_id: str
    raw_score: float
    emission: str
    policy_state: str
    raw_fraction_pct: float | None
    measurement_available: bool
    requires_event_history: bool
    requires_entity_scope: bool
    entity_key: str | None
    overlap_tags: tuple[str, ...]
    issues: tuple[str, ...]
    notes: tuple[str, ...]

    # Replay calibration 계층.
    #
    # 기존 필드 뒤에 default를 두어 기존 호출부와의 호환성을 유지한다.
    calibration_version: str | None = None
    calibration_mode: str | None = None
    calibration_threshold: float | None = None
    threshold_met: bool | None = None


@dataclass(frozen=True)
class PlayerRiskInput:
    """플레이어 1명의 Aggregate Risk 계산 직전 입력.

    이 객체 자체에는 아직 weight, aggregate score, probability,
    final verdict가 없다.
    """

    session_id: str
    player_id: str
    signals: tuple[RiskSignalInput, ...]
    measurement_unavailable_modules: tuple[str, ...]
    unresolved_policy_modules: tuple[str, ...]
    event_history_modules: tuple[str, ...]
    entity_scoped_modules: tuple[str, ...]
    correlation_candidates: tuple[CorrelationCandidate, ...]
    missing_modules: tuple[str, ...] = ()
    stale_modules: tuple[str, ...] = ()


def _is_unresolved(
    policy_state: str,
    calibration: ModuleCalibration | None,
) -> bool:
    """현재 signal이 Aggregate Risk 전에 추가 정책 결정이 필요한지 판단한다."""

    # 구현/측정 범위 자체가 신뢰되지 않는 상태는 calibration threshold로 덮지 않는다.
    if policy_state in _HARD_UNRESOLVED_POLICY_STATES:
        return True

    # calibration config 자체에 없는 신규 detector.
    if calibration is None:
        return True

    # 데이터 추가를 기다리는 공식 pending detector.
    if calibration.mode == "pending":
        return True

    # threshold/event_threshold/advisory는 replay-v1에서 의미가 확정됐다.
    #
    # 따라서 기존 Policy 층의 POLICY_NOT_CALIBRATED 상태여도
    # 이 calibration 계층에서는 unresolved로 남기지 않는다.
    return False


def _threshold_result(
    *,
    raw_score: float,
    policy_state: str,
    measurement_available: bool,
    calibration: ModuleCalibration | None,
) -> bool | None:
    """현재 raw sample의 calibration threshold 충족 여부.

    None:
    - 측정 불가
    - pending/advisory
    - unknown calibration
    - audited range 위반 등 Policy 단계에서 사용하면 안 되는 값
    """

    if not measurement_available:
        return None

    if policy_state in _HARD_UNRESOLVED_POLICY_STATES:
        return None

    if calibration is None:
        return None

    return calibration.meets_threshold(raw_score)


def build_player_risk_input(snapshot: PlayerPolicySnapshot) -> PlayerRiskInput:
    """PlayerPolicySnapshot을 calibration이 연결된 risk 입력으로 변환한다.

    snapshot raw_score는 합산하지 않는다.

    event_delta/window_history는 history가 필요하다는 표시를 유지하며,
    calibration threshold를 통과했다고 해서 최신 한 건만으로 최종 risk를
    확정하지 않는다.
    """

    signals: list[RiskSignalInput] = []
    unavailable: list[str] = []
    unresolved: list[str] = []
    history_required: list[str] = []
    entity_scoped: list[str] = []

    for item in snapshot.modules:
        state = item.state
        evaluation = item.evaluation
        signal = evaluation.signal
        annotations = evaluation.annotations

        if state.module != signal.module:
            raise ValueError(
                "module state and policy evaluation do not match"
            )

        if state.module in _OPERATIONAL_ONLY_MODULES:
            continue

        measurement_available = (
            signal.state not in _MEASUREMENT_UNAVAILABLE_STATES
        )

        requires_event_history = signal.emission in (
            "event_delta",
            "window_history",
        )

        requires_entity_scope = signal.emission in (
            "per_entity_positive_only",
            "per_entity_snapshot",
        )

        calibration = resolve_calibration(
            signal.module,
            raw_score=signal.raw_score,
            evidence=state.evidence,
        )

        threshold_met = _threshold_result(
            raw_score=signal.raw_score,
            policy_state=signal.state,
            measurement_available=measurement_available,
            calibration=calibration,
        )

        calibration_threshold = None
        calibration_version = None
        calibration_mode = None

        if calibration is not None:
            calibration_version = calibration.version
            calibration_mode = calibration.mode

            if calibration.threshold is not None:
                calibration_threshold = float(calibration.threshold)

        if not measurement_available:
            unavailable.append(signal.module)

        if _is_unresolved(signal.state, calibration):
            unresolved.append(signal.module)

        if requires_event_history:
            history_required.append(signal.module)

        if requires_entity_scope:
            entity_scoped.append(signal.module)

        signals.append(
            RiskSignalInput(
                module=signal.module,
                event_id=state.event_id,
                raw_score=signal.raw_score,
                emission=signal.emission,
                policy_state=signal.state,
                raw_fraction_pct=signal.raw_fraction_pct,
                measurement_available=measurement_available,
                requires_event_history=requires_event_history,
                requires_entity_scope=requires_entity_scope,
                entity_key=annotations.entity_key,
                overlap_tags=annotations.overlap_tags,
                issues=signal.issues,
                notes=annotations.notes,
                calibration_version=calibration_version,
                calibration_mode=calibration_mode,
                calibration_threshold=calibration_threshold,
                threshold_met=threshold_met,
            )
        )

    return PlayerRiskInput(
        session_id=snapshot.session_id,
        player_id=snapshot.player_id,
        signals=tuple(signals),
        measurement_unavailable_modules=tuple(unavailable),
        unresolved_policy_modules=tuple(unresolved),
        event_history_modules=tuple(history_required),
        entity_scoped_modules=tuple(entity_scoped),
        correlation_candidates=tuple(
            candidate
            for candidate in snapshot.correlation_candidates
            if not _OPERATIONAL_ONLY_MODULES.intersection(candidate.modules)
        ),
        missing_modules=snapshot.missing_modules,
        stale_modules=snapshot.stale_modules,
    )
