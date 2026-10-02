"""AutoPaint의 무결성/행동 채널을 분석하는 정책 주석."""

from __future__ import annotations

from typing import Any, Mapping

from .contract import PolicyAnnotations
from ..policy import SignalPreview


def evaluate(event: Mapping[str, Any], baseline: SignalPreview) -> PolicyAnnotations:
    """AutoPaint 두 채널의 유효성과 다른 탐지기와의 중복 후보를 표시한다."""
    evidence = event["evidence"]
    tags: list[str] = []
    notes = [
        "AutoPaint raw_score는 유효한 integrity/behavior 채널 점수의 합이 아니라 최댓값이다.",
        "한 채널이 무효여도 다른 채널이 유효하면 그 채널의 스냅샷을 사용할 수 있다.",
    ]

    # 파일/모듈 흔적은 LocalGuard filesystem/YARA 계열과 관측이 겹칠 수 있다.
    if any(
        evidence.get(name)
        for name in (
            "known_bridge_hash",
            "autopaint_marker_set",
            "autopaint_runtime_module",
            "runtime_artifact",
        )
    ):
        tags.append("autopaint_artifact")

    # 프로세스/스레드 기반 근거는 injection/external_access와의 상관 후보가 된다.
    if any(
        evidence.get(name)
        for name in (
            "unsigned_user_module",
            "suspicious_module_thread",
            "injector_process",
            "controller_process",
        )
    ):
        tags.append("process_injection")

    if evidence.get("loopback_tcp_endpoint") or evidence.get("matched_port_sidecar"):
        tags.append("loopback_ipc")
    if evidence.get("behavior_detected"):
        tags.append("autopaint_behavior")

    integrity_valid = evidence.get("integrity_valid")
    behavior_valid = evidence.get("behavior_valid")
    if integrity_valid == 0 and behavior_valid == 0:
        notes.append("두 채널 모두 무효인 샘플은 정상 0점이 아니라 측정 불가 상태다.")
    elif integrity_valid == 0 or behavior_valid == 0:
        notes.append("일부 채널만 유효한 부분 관측이므로 다른 채널의 0점을 정상 근거로 확대 해석하지 않는다.")
    if baseline.state == "MEASUREMENT_UNAVAILABLE":
        notes.append("측정 불가 상태에서는 raw_fraction_pct를 최종 위험도로 사용하지 않는다.")

    return PolicyAnnotations(overlap_tags=tuple(tags), notes=tuple(notes))
