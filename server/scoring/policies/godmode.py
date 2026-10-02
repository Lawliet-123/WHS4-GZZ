"""Godmode Shared event에 대한 읽기 전용 Policy 주석.

Godmode의 raw_score는 누적 점수가 아니라 해당 Snapshot에서 새로 발생한
탐지 점수(result.new_score)다. 중앙 Shared 전송도 new_reasons가 생긴
이벤트만 보내므로 latest_state 한 건만으로 누적 사건을 판단하면 안 된다.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..policy import SignalPreview
from .contract import PolicyAnnotations


def evaluate(
    event: Mapping[str, Any],
    signal: SignalPreview,
) -> PolicyAnnotations:
    """Godmode event_delta 의미를 보존하고 상관 후보용 메타데이터만 추가한다."""
    evidence = event["evidence"]
    tags: list[str] = []

    if event["raw_score"] > 0 or event["reasons"]:
        tags.append("godmode_behavior")

    if evidence.get("invincible") is True:
        tags.append("invincibility_state")

    if (
        evidence.get("change_before_health") is not None
        or evidence.get("kill_event") is True
        or evidence.get("death_event") is True
        or evidence.get("heal_event") is True
        or evidence.get("respawn_event") is True
    ):
        tags.append("health_state_transition")

    notes = (
        "Godmode raw_score는 누적 점수가 아니라 이번 Snapshot의 새 사건 점수(result.new_score)다.",
        "Shared 중앙 전송은 new_reasons가 생긴 이벤트만 보내므로 latest_state 한 건만으로 누적 사건을 판단하지 않는다.",
        "event_id 중복 제거가 적용된 event_delta_history를 후속 risk 계산의 사건 이력으로 사용해야 한다.",
    )

    return PolicyAnnotations(
        overlap_tags=tuple(tags),
        notes=notes,
    )
