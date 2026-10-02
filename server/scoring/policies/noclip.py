"""Noclip 탐지기의 Shared Event에 분석용 주석을 붙인다.

이 파일은 원시 점수나 최종 위험도를 변경하지 않는다. Noclip과 다른 채널이
같은 현상을 관측했을 가능성을 나중에 비교할 수 있도록 후보 태그만 반환한다.
"""

from __future__ import annotations

from typing import Any, Mapping

from .contract import PolicyAnnotations
from ..policy import SignalPreview


def evaluate(event: Mapping[str, Any], baseline: SignalPreview) -> PolicyAnnotations:
    """Noclip 스냅샷의 관측 근거와 중복 후보를 설명한다."""
    evidence = event["evidence"]
    reasons = set(event["reasons"])
    tags: list[str] = []
    notes = [
        "Noclip raw_score는 현재 샘플의 스냅샷이므로 반복 수신값을 누적 합산하지 않는다.",
    ]

    # LocalGuard noclip_runtime이 같은 충돌/이동 이상을 볼 수 있으므로
    # 최종 중복 판정이 아니라 '검토 후보'라는 의미의 태그만 남긴다.
    if event["raw_score"] > 0 or evidence.get("detection_hold_active") is True:
        tags.append("noclip_behavior")
    if evidence.get("collision") == 0 or {
        "Collision Disabled",
        "Collision Disabled Too Long",
    } & reasons:
        tags.append("collision_disabled")
    if evidence.get("blocked_path") == 1 or "Blocked Path Detected" in reasons:
        tags.append("blocked_path")

    if evidence.get("detection_hold_active") is True:
        notes.append(
            "5초 hold 동안 status는 SUSPICIOUS일 수 있지만 raw_score에는 과거 탐지 점수를 재가산하지 않는다."
        )
    if baseline.state == "MEASUREMENT_UNAVAILABLE":
        notes.append("ERROR 측정은 정상 0점으로 해석하지 않는다.")

    return PolicyAnnotations(overlap_tags=tuple(tags), notes=tuple(notes))
