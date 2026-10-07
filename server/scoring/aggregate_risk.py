"""Overlap 보정 이후의 Aggregate Risk 요약.

현재 버전은 detector별 임의 weight나 0~100 확률 점수를 만들지 않는다.

대신:
- 독립 ACTIVE signal 1개 = evidence unit 1개
- overlap cluster 1개 = evidence unit 1개
- 같은 원인 가능성이 있는 여러 module을 중복 계산하지 않는다.

이 값은 Final Verdict가 아니라 그 직전의 종합 risk 근거다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fusion import FusionEvidenceUnit, FusionPlan


AGGREGATE_RISK_VERSION = "evidence-units-v1"


@dataclass(frozen=True)
class AggregateRisk:
    version: str

    session_id: str
    player_id: str

    evidence_units: tuple[FusionEvidenceUnit, ...]

    evidence_unit_count: int
    active_module_count: int

    independent_unit_count: int
    overlap_cluster_count: int

    # overlap 때문에 별도 독립 근거로 세지 않은 module 수.
    overlap_adjustment_count: int

    # False이면 "현재 활성 근거가 없다"를 곧바로 정상 판정으로 확대하면 안 된다.
    assessment_complete: bool

    active_modules: tuple[str, ...]
    independent_modules: tuple[str, ...]
    clustered_modules: tuple[str, ...]

    advisory_modules: tuple[str, ...]
    unresolved_modules: tuple[str, ...]
    deferred_modules: tuple[str, ...]
    unavailable_modules: tuple[str, ...]
    missing_modules: tuple[str, ...] = ()
    stale_modules: tuple[str, ...] = ()


def build_aggregate_risk(
    plan: FusionPlan,
) -> AggregateRisk:
    """FusionPlan을 중복 보정된 Aggregate Risk 근거로 변환한다."""

    if not isinstance(plan, FusionPlan):
        raise TypeError("plan must be FusionPlan")

    represented: list[str] = []

    independent_count = 0
    cluster_count = 0

    for unit in plan.units:
        if not unit.modules:
            raise ValueError("fusion evidence unit must contain a module")

        represented.extend(unit.modules)

        if unit.kind == "INDEPENDENT":
            if len(unit.modules) != 1:
                raise ValueError(
                    "INDEPENDENT fusion unit must contain exactly one module"
                )
            independent_count += 1

        elif unit.kind == "OVERLAP_CLUSTER":
            if len(unit.modules) < 2:
                raise ValueError(
                    "OVERLAP_CLUSTER must contain at least two modules"
                )
            cluster_count += 1

        else:
            raise ValueError(
                f"unsupported fusion unit kind: {unit.kind}"
            )

    if len(represented) != len(set(represented)):
        raise ValueError(
            "an active module is represented by multiple fusion units"
        )

    active = set(plan.active_modules)

    if set(represented) != active:
        raise ValueError(
            "fusion units must represent every active module exactly once"
        )

    evidence_unit_count = len(plan.units)
    active_module_count = len(plan.active_modules)

    overlap_adjustment_count = (
        active_module_count - evidence_unit_count
    )

    if overlap_adjustment_count < 0:
        raise ValueError(
            "evidence unit count cannot exceed active module count"
        )

    assessment_complete = not (
        plan.unresolved_modules
        or plan.deferred_modules
        or plan.unavailable_modules
        or plan.missing_modules
        or plan.stale_modules
    )

    return AggregateRisk(
        version=AGGREGATE_RISK_VERSION,
        session_id=plan.session_id,
        player_id=plan.player_id,
        evidence_units=plan.units,
        evidence_unit_count=evidence_unit_count,
        active_module_count=active_module_count,
        independent_unit_count=independent_count,
        overlap_cluster_count=cluster_count,
        overlap_adjustment_count=overlap_adjustment_count,
        assessment_complete=assessment_complete,
        active_modules=plan.active_modules,
        independent_modules=plan.independent_modules,
        clustered_modules=plan.clustered_modules,
        advisory_modules=plan.advisory_modules,
        unresolved_modules=plan.unresolved_modules,
        deferred_modules=plan.deferred_modules,
        unavailable_modules=plan.unavailable_modules,
        missing_modules=plan.missing_modules,
        stale_modules=plan.stale_modules,
    )
