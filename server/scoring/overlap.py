"""Aggregate Risk 전 단계의 overlap 후보 그룹화.

CorrelationCandidate를 사용해 서로 연결된 ACTIVE 모듈을 그룹으로 묶는다.

중요:
- 그룹은 '같은 치트 사건으로 확정'했다는 뜻이 아니다.
- 점수를 감산하거나 하나를 삭제하지 않는다.
- 공통 overlap_tag가 실제 correlation 후보에 존재할 때만 연결한다.
- Aggregate/Final Verdict가 중복 가능성을 명시적으로 볼 수 있도록 구조화만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .correlation import CorrelationCandidate


@dataclass(frozen=True)
class OverlapGroup:
    modules: tuple[str, ...]
    overlap_tags: tuple[str, ...]
    candidate_count: int
    event_ids: tuple[str, ...]


def build_overlap_groups(
    candidates: Iterable[CorrelationCandidate],
    *,
    active_modules: Iterable[str],
) -> tuple[OverlapGroup, ...]:
    """ACTIVE 모듈 사이의 correlation 후보를 connected component로 묶는다."""

    active = set(active_modules)

    # module -> 인접 module
    graph: dict[str, set[str]] = {}

    usable: list[CorrelationCandidate] = []

    for candidate in candidates:
        left, right = candidate.modules

        # Aggregate Risk에서 실제 활성 증거끼리만 overlap 후보로 사용한다.
        if left not in active or right not in active:
            continue

        if not candidate.overlap_tags:
            continue

        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)
        usable.append(candidate)

    if not graph:
        return ()

    visited: set[str] = set()
    groups: list[OverlapGroup] = []

    for start in sorted(graph):
        if start in visited:
            continue

        stack = [start]
        component: set[str] = set()

        while stack:
            current = stack.pop()

            if current in visited:
                continue

            visited.add(current)
            component.add(current)

            stack.extend(
                neighbor
                for neighbor in graph.get(current, ())
                if neighbor not in visited
            )

        if len(component) < 2:
            continue

        relevant = [
            candidate
            for candidate in usable
            if set(candidate.modules).issubset(component)
        ]

        tags = sorted(
            {
                tag
                for candidate in relevant
                for tag in candidate.overlap_tags
            }
        )

        event_ids = sorted(
            {
                event_id
                for candidate in relevant
                for event_id in candidate.event_ids
            }
        )

        groups.append(
            OverlapGroup(
                modules=tuple(sorted(component)),
                overlap_tags=tuple(tags),
                candidate_count=len(relevant),
                event_ids=tuple(event_ids),
            )
        )

    return tuple(groups)
