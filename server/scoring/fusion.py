"""Aggregate Risk 계산 전의 evidence fusion 계획.

이 계층은 ACTIVE evidence를 다음 두 종류로 구조화한다.

- INDEPENDENT: 현재 overlap 후보에 포함되지 않은 ACTIVE module
- OVERLAP_CLUSTER: correlation/overlap 후보로 서로 연결된 ACTIVE modules

중요:
- OVERLAP_CLUSTER는 같은 사건으로 확정했다는 뜻이 아니다.
- module을 삭제하지 않는다.
- 점수를 합산하거나 감산하지 않는다.
- weight, probability, Final Verdict를 계산하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .aggregate import AggregateEvidence


FusionUnitKind = Literal[
    "INDEPENDENT",
    "OVERLAP_CLUSTER",
]


@dataclass(frozen=True)
class FusionEvidenceUnit:
    kind: FusionUnitKind
    modules: tuple[str, ...]
    overlap_tags: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    candidate_count: int = 0


@dataclass(frozen=True)
class FusionPlan:
    session_id: str
    player_id: str

    units: tuple[FusionEvidenceUnit, ...]

    active_modules: tuple[str, ...]
    clustered_modules: tuple[str, ...]
    independent_modules: tuple[str, ...]

    unresolved_modules: tuple[str, ...]
    deferred_modules: tuple[str, ...]
    unavailable_modules: tuple[str, ...]
    advisory_modules: tuple[str, ...] = ()


def build_fusion_plan(
    evidence: AggregateEvidence,
) -> FusionPlan:
    """AggregateEvidence를 중복 반영 전 fusion 계획으로 변환한다."""

    if not isinstance(evidence, AggregateEvidence):
        raise TypeError("evidence must be AggregateEvidence")

    active = set(evidence.active_modules)

    clustered: set[str] = set()
    units: list[FusionEvidenceUnit] = []

    # overlap group은 이미 ACTIVE module만으로 구성되어 있다.
    for group in evidence.overlap_groups:
        modules = tuple(
            module
            for module in group.modules
            if module in active
        )

        if len(modules) < 2:
            continue

        duplicate = clustered.intersection(modules)
        if duplicate:
            raise ValueError(
                "an active module appears in multiple overlap groups: "
                + ", ".join(sorted(duplicate))
            )

        clustered.update(modules)

        units.append(
            FusionEvidenceUnit(
                kind="OVERLAP_CLUSTER",
                modules=modules,
                overlap_tags=group.overlap_tags,
                event_ids=group.event_ids,
                candidate_count=group.candidate_count,
            )
        )

    independent = tuple(
        sorted(active - clustered)
    )

    for module in independent:
        signal = next(
            item
            for item in evidence.signals
            if item.module == module
        )

        units.append(
            FusionEvidenceUnit(
                kind="INDEPENDENT",
                modules=(module,),
                event_ids=(signal.event_id,),
            )
        )

    # 출력 순서를 항상 재현 가능하게 고정한다.
    units.sort(
        key=lambda item: (
            item.kind,
            item.modules,
            item.event_ids,
        )
    )

    return FusionPlan(
        session_id=evidence.session_id,
        player_id=evidence.player_id,
        units=tuple(units),
        active_modules=tuple(sorted(active)),
        clustered_modules=tuple(sorted(clustered)),
        independent_modules=independent,
        unresolved_modules=evidence.unresolved_modules,
        deferred_modules=evidence.deferred_modules,
        unavailable_modules=evidence.unavailable_modules,
        advisory_modules=evidence.advisory_modules,
    )
