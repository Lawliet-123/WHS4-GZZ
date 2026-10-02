"""최종 risk 계산 전에 Policy 결과를 안전한 공통 입력으로 정리한다.

이 계층은 서로 다른 detector 점수를 합산하거나 가중치를 부여하지 않는다.
측정 실패, 미확정 정책, 사건 이력이 필요한 모듈처럼 후속 계산에서 반드시
구분해야 할 상태를 명시적으로 표시하는 역할만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .correlation import CorrelationCandidate
from .player_snapshot import PlayerPolicySnapshot


# 실제 측정값을 정상 표본처럼 사용하면 안 되는 상태다.
_MEASUREMENT_UNAVAILABLE_STATES = frozenset({
    "MEASUREMENT_UNAVAILABLE",
})

# 최종 risk 규칙에 바로 넣기 전에 정책/구현 확인이 더 필요한 상태다.
_UNRESOLVED_POLICY_STATES = frozenset({
    "UNKNOWN_MODULE",
    "AWAITING_DETECTOR",
    "POLICY_NOT_CALIBRATED",
    "OUT_OF_AUDITED_RANGE",
})


@dataclass(frozen=True)
class RiskSignalInput:
    """모듈 1개의 현재 Policy 결과를 risk 계산 전 입력으로 고정한 값."""

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


@dataclass(frozen=True)
class PlayerRiskInput:
    """플레이어 1명의 최종 risk 계산 전 입력 묶음.

    이 자료형에는 weight, aggregate score, probability, verdict가 없다.
    """

    session_id: str
    player_id: str
    signals: tuple[RiskSignalInput, ...]
    measurement_unavailable_modules: tuple[str, ...]
    unresolved_policy_modules: tuple[str, ...]
    event_history_modules: tuple[str, ...]
    entity_scoped_modules: tuple[str, ...]
    correlation_candidates: tuple[CorrelationCandidate, ...]


def build_player_risk_input(snapshot: PlayerPolicySnapshot) -> PlayerRiskInput:
    """PlayerPolicySnapshot을 후속 risk 계산용 공통 입력으로 변환한다.

    snapshot 계열 raw_score는 그대로 보존하고 합산하지 않는다.
    event_delta 계열은 최신 한 건만으로 계산하지 않도록 history 필요 표시를 남긴다.
    ERROR/OFFLINE 등 측정 불가 상태의 0점도 정상 0점으로 바꾸지 않는다.
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

        # 저장된 module과 Policy가 해석한 module이 다르면 잘못 연결된 입력이다.
        if state.module != signal.module:
            raise ValueError("module state and policy evaluation do not match")

        measurement_available = (
            signal.state not in _MEASUREMENT_UNAVAILABLE_STATES
        )
        requires_event_history = signal.emission == "event_delta"
        requires_entity_scope = signal.emission == "per_entity_positive_only"

        if not measurement_available:
            unavailable.append(signal.module)
        if signal.state in _UNRESOLVED_POLICY_STATES:
            unresolved.append(signal.module)
        if requires_event_history:
            history_required.append(signal.module)
        if requires_entity_scope:
            entity_scoped.append(signal.module)

        signals.append(RiskSignalInput(
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
        ))

    return PlayerRiskInput(
        session_id=snapshot.session_id,
        player_id=snapshot.player_id,
        signals=tuple(signals),
        measurement_unavailable_modules=tuple(unavailable),
        unresolved_policy_modules=tuple(unresolved),
        event_history_modules=tuple(history_required),
        entity_scoped_modules=tuple(entity_scoped),
        correlation_candidates=snapshot.correlation_candidates,
    )
