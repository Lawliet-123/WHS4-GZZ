"""B Scoring의 보수적 Final Verdict 계층.

AggregateRisk를 최종 소비 가능한 판정 상태로 변환한다.

현재 verdict는 다음 세 상태만 사용한다.

- SUSPICIOUS:
    overlap 보정 후 calibrated ACTIVE evidence unit이 1개 이상 존재한다.

- INCONCLUSIVE:
    ACTIVE evidence는 없지만 unresolved / deferred / unavailable 신호가 있어
    현재 정보만으로 음성 판정을 내릴 수 없다.

- NO_ACTIVE_EVIDENCE:
    현재 평가 가능한 범위에서는 ACTIVE evidence가 없다.

중요:
- SUSPICIOUS는 '치트 확정'을 의미하지 않는다.
- NO_ACTIVE_EVIDENCE는 전체 detector coverage가 정상임을 증명하지 않는다.
- CHEAT 확정, 확률, 임의 0~100 점수는 만들지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .aggregate_risk import AggregateRisk


FINAL_VERDICT_VERSION = "conservative-v1"

FinalVerdictStatus = Literal[
    "SUSPICIOUS",
    "INCONCLUSIVE",
    "NO_ACTIVE_EVIDENCE",
]


@dataclass(frozen=True)
class FinalVerdict:
    version: str

    session_id: str
    player_id: str

    status: FinalVerdictStatus

    # Aggregate 단계가 현재 관측 중 unresolved/deferred/unavailable 없이 끝났는지.
    assessment_complete: bool

    evidence_unit_count: int
    active_module_count: int
    overlap_adjustment_count: int

    active_modules: tuple[str, ...]
    advisory_modules: tuple[str, ...]

    unresolved_modules: tuple[str, ...]
    deferred_modules: tuple[str, ...]
    unavailable_modules: tuple[str, ...]

    # C/Dashboard가 판정 이유를 문자열 파싱 없이 볼 수 있는 안정적인 코드.
    reason_codes: tuple[str, ...]


def build_final_verdict(
    risk: AggregateRisk,
) -> FinalVerdict:
    """AggregateRisk를 보수적 Final Verdict로 변환한다."""

    if not isinstance(risk, AggregateRisk):
        raise TypeError("risk must be AggregateRisk")

    reasons: list[str] = []

    # Positive evidence는 assessment가 일부 불완전하더라도 사라지지 않는다.
    if risk.evidence_unit_count > 0:
        status: FinalVerdictStatus = "SUSPICIOUS"
        reasons.append("CALIBRATED_ACTIVE_EVIDENCE")

        if not risk.assessment_complete:
            reasons.append("ASSESSMENT_INCOMPLETE")

    # Positive evidence가 없을 때 불완전한 측정을 NORMAL로 취급하지 않는다.
    elif not risk.assessment_complete:
        status = "INCONCLUSIVE"
        reasons.append("ASSESSMENT_INCOMPLETE")

    else:
        status = "NO_ACTIVE_EVIDENCE"
        reasons.append("NO_ACTIVE_EVIDENCE")

    if risk.advisory_modules:
        reasons.append("ADVISORY_EVIDENCE_PRESENT")

    return FinalVerdict(
        version=FINAL_VERDICT_VERSION,
        session_id=risk.session_id,
        player_id=risk.player_id,
        status=status,
        assessment_complete=risk.assessment_complete,
        evidence_unit_count=risk.evidence_unit_count,
        active_module_count=risk.active_module_count,
        overlap_adjustment_count=risk.overlap_adjustment_count,
        active_modules=risk.active_modules,
        advisory_modules=risk.advisory_modules,
        unresolved_modules=risk.unresolved_modules,
        deferred_modules=risk.deferred_modules,
        unavailable_modules=risk.unavailable_modules,
        reason_codes=tuple(reasons),
    )
