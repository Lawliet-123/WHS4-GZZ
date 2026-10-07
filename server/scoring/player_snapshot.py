"""플레이어의 현재 모듈 상태와 Policy 평가를 한 번에 묶는 읽기 전용 뷰.

이 모듈은 latest_state의 현재 기록을 다시 점수화하거나 합산하지 않는다.
각 모듈의 최신 Event를 등록된 Policy로 해석하고, 요청한 경우에만 같은 최신
관측들 사이의 correlation candidate를 함께 계산한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .correlation import (
    CorrelationCandidate,
    find_correlation_candidates,
    observation_from_evaluation,
)
from .policies.contract import PolicyEvaluation
from .policies.registry import evaluate_registered_policy
from .storage import ModuleState


@dataclass(frozen=True)
class ModulePolicySnapshot:
    """모듈 1개의 최신 저장 상태와 그 상태에 대한 Policy 평가."""

    state: ModuleState
    evaluation: PolicyEvaluation


@dataclass(frozen=True)
class PlayerPolicySnapshot:
    """플레이어 1명의 현재 Policy 분석 뷰.

    modules는 latest_state에 존재하는 모듈별 최신 1건이다.
    correlation_candidates는 요청자가 시간 창을 지정한 경우에만 계산한다.
    이 자료형 자체에는 최종 risk, weight, verdict가 없다.
    """

    session_id: str
    player_id: str
    modules: tuple[ModulePolicySnapshot, ...]
    correlation_candidates: tuple[CorrelationCandidate, ...]
    missing_modules: tuple[str, ...] = ()
    stale_modules: tuple[str, ...] = ()


def _event_from_state(state: ModuleState) -> dict:
    """ModuleState를 공통 7필드 Event 형태로 복원한다."""
    return {
        "session_id": state.session_id,
        "player_id": state.player_id,
        "module": state.module,
        "timestamp_ms": state.timestamp_ms,
        "evidence": state.evidence,
        "reasons": state.reasons,
        "raw_score": state.raw_score,
    }


def build_player_policy_snapshot(
    states: Iterable[ModuleState],
    *,
    session_id: str,
    player_id: str,
    max_time_distance_ms: int | None = None,
    expected_modules: Iterable[str] | None = None,
    observed_at_ms: int | None = None,
    max_age_ms: int | None = None,
) -> PlayerPolicySnapshot:
    """현재 모듈 상태들을 Policy 평가가 포함된 플레이어 뷰로 변환한다.

    max_time_distance_ms를 생략하면 correlation은 계산하지 않는다. 아직 팀에서
    합의된 기본 시간 창이 없으므로 임의의 5초 같은 값을 자동 적용하지 않는다.
    """
    if max_time_distance_ms is not None and (
        type(max_time_distance_ms) is not int or max_time_distance_ms < 0
    ):
        raise ValueError("max_time_distance_ms must be None or a nonnegative integer")

    if (observed_at_ms is None) != (max_age_ms is None):
        raise ValueError(
            "observed_at_ms and max_age_ms must be provided together"
        )

    if observed_at_ms is not None and (
        type(observed_at_ms) is not int or observed_at_ms < 0
    ):
        raise ValueError("observed_at_ms must be a nonnegative integer")

    if max_age_ms is not None and (
        type(max_age_ms) is not int or max_age_ms < 0
    ):
        raise ValueError("max_age_ms must be a nonnegative integer")

    expected = tuple(expected_modules or ())
    if any(
        not isinstance(module, str) or not module
        for module in expected
    ):
        raise ValueError(
            "expected_modules must contain nonempty strings"
        )

    entries: list[ModulePolicySnapshot] = []
    observations = []

    # 저장소 반환 순서에 의존하지 않도록 모듈/시간/sequence 순으로 고정한다.
    ordered_states = sorted(
        states,
        key=lambda item: (item.module, item.timestamp_ms, item.sequence, item.event_id),
    )

    observed_modules = {state.module for state in ordered_states}

    missing_modules = tuple(sorted(
        set(expected) - observed_modules
    ))

    stale_modules: tuple[str, ...] = ()
    if observed_at_ms is not None and max_age_ms is not None:
        stale_modules = tuple(sorted({
            state.module
            for state in ordered_states
            if (
                state.module in expected
                and observed_at_ms - state.timestamp_ms > max_age_ms
            )
        }))

    for state in ordered_states:
        if state.session_id != session_id or state.player_id != player_id:
            raise ValueError("module state does not belong to requested session/player")

        event = _event_from_state(state)
        evaluation = evaluate_registered_policy(event)
        entries.append(ModulePolicySnapshot(state=state, evaluation=evaluation))

        if max_time_distance_ms is not None:
            observations.append(observation_from_evaluation(
                event,
                event_id=state.event_id,
                sequence=state.sequence,
                evaluation=evaluation,
            ))

    candidates: tuple[CorrelationCandidate, ...] = ()
    if max_time_distance_ms is not None:
        candidates = tuple(find_correlation_candidates(
            observations,
            max_time_distance_ms=max_time_distance_ms,
        ))

    return PlayerPolicySnapshot(
        session_id=session_id,
        player_id=player_id,
        modules=tuple(entries),
        missing_modules=missing_modules,
        stale_modules=stale_modules,
        correlation_candidates=candidates,
    )
