"""Aimbot 탐지기의 라운드 누적 스냅샷을 분석하는 정책 주석."""

from __future__ import annotations

from typing import Any, Mapping

from .contract import PolicyAnnotations
from ..policy import SignalPreview


def evaluate(event: Mapping[str, Any], baseline: SignalPreview) -> PolicyAnnotations:
    """Aimbot의 라운드 스냅샷 성격과 중복 후보 태그를 반환한다."""
    evidence = event["evidence"]
    reasons = set(event["reasons"])
    tags: list[str] = []
    notes = [
        "Aimbot raw_score는 현재 라운드의 누적 관측 스냅샷이며 사건별 증분 점수가 아니다.",
        "같은 점수나 이후 점수를 반복 수신해도 합산하지 않고 최신 유효 스냅샷으로 해석한다.",
    ]

    # LocalGuard aimbot_runtime 등 다른 조준 관측 채널과의 상관 분석을 위한 후보 태그다.
    if event["raw_score"] > 0:
        tags.append("aimbot_behavior")
    if {
        "Consistent Target Convergence Before Confirmed Find",
        "Target Lock Maintained Before Confirmed Find",
    } & reasons:
        tags.append("target_convergence")
    if "Precise Tracking Maintained While Hidden Target Moved" in reasons:
        tags.append("blind_target_tracking")

    if evidence.get("status") == "ERROR":
        notes.append("라운드 범위 미확정 ERROR 0점은 정상 스냅샷으로 사용하지 않는다.")

    return PolicyAnnotations(overlap_tags=tuple(tags), notes=tuple(notes))
