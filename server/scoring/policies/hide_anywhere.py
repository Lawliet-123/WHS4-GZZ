"""현재 Hide Anywhere 생산자와 과거 리플레이를 구분하는 읽기 전용 정책."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..policy import SignalPreview
from .contract import PolicyAnnotations
from .overlap import HIDE_INJECTION, HIDE_VALUE_TAMPER

CONFIRMED_REASON = "Hide Anywhere Value Pattern Confirmed (3 Consecutive Samples)"
PENDING_REASON = "Hide Anywhere Value Pattern Pending Confirmation"
LEGACY_REASON = "Hide Anywhere Value Pattern Matched"
_FLAGS = ("hide_value_pattern", "injected_module", "viewport_hook")
_KNOWN_REASONS = frozenset((
    CONFIRMED_REASON, PENDING_REASON, LEGACY_REASON, "Observation Unavailable",
    "Injected Module Loaded", "Viewport VTable Outside Main Image",
))


def _flag(value: Any) -> bool:
    return type(value) is int and value in (0, 1)


def evaluate(event: Mapping[str, Any], baseline: SignalPreview) -> PolicyAnnotations:
    """원점수와 baseline을 보존하고 생산자가 보고한 확인/실패 의미만 해석한다."""
    if event["module"] != "hide_anywhere":
        raise ValueError("Hide Anywhere policy supports only hide_anywhere")
    if baseline.module != event["module"]:
        raise ValueError("baseline module does not match the event")
    notes = [
        "Hide Anywhere는 현재 설정값 snapshot이다. 반복 3점을 신규 사건으로 누적하지 않는다.",
        "고정값 조합 관측은 실제 숨기 성공·거리 우회 행동의 증명이 아니다.",
        "Event에 안정된 PID/Pawn 사건 ID가 없다. 회차·로그 경로로 entity_key를 만들지 않는다.",
        "overlap_tags는 실제 대응 근거가 있는 상관 후보다. 후보 자체로 중복 확정·감산하지 않는다.",
    ]
    if baseline.state == "MEASUREMENT_UNAVAILABLE":
        notes.append("ERROR/OFFLINE/measurement_valid=false는 정상 0점이 아니다. 남은 근거를 유효 관측으로 승격하거나 overlap tag를 만들지 않는다.")
        return PolicyAnnotations(notes=tuple(notes))

    evidence, reasons = event["evidence"], set(event["reasons"])
    if evidence.get("status") == "WARNING" or evidence.get("coverage_complete") is False:
        notes.append("부분 검사다. 확인된 근거를 보존하되 전체 정상으로 확정하지 않는다.")
    if evidence.get("measurement_valid") is not True:
        notes.append("측정 유효성이 명시되지 않았다. 과거 형식만으로 읽기 성공·신선도를 보장하지 않으며 이전 값이 남았을 수도 있다.")
    else:
        notes.append("measurement_valid=true는 생산자의 유효성 보고이며 중앙에서 메모리를 독립적으로 재검사한 것은 아니다.")

    tags: list[str] = []
    current = "hide_value_confirmed" in evidence
    valid_flags = all(_flag(evidence.get(name)) for name in _FLAGS)
    if current:
        # 핵심 값의 3회 확인은 부가 모듈/Viewport 조회 실패와 별개다.
        valid_flags = _flag(evidence.get("hide_value_pattern")) and _flag(evidence.get("hide_value_confirmed"))
    if not valid_flags:
        notes.append("0/1 정수 플래그가 누락되거나 형식이 다르다. None/bool/문자열을 정상 0점으로 보완하지 않는다.")
    else:
        pattern = evidence["hide_value_pattern"]
        injected, viewport = (evidence.get(name) for name in _FLAGS[1:])
        auxiliary_score = sum(value for value in (injected, viewport) if _flag(value))
        confirmed = evidence.get("hide_value_confirmed", 0)
        confirmation_valid = False
        if current:
            count, required = evidence.get("consecutive_matches"), evidence.get("required_matches")
            counts_valid = type(count) is int and count >= 0 and type(required) is int and required == 3
            confirmation_valid = bool(
                counts_valid and count >= required and pattern == confirmed == 1
                and evidence.get("measurement_valid") is True and CONFIRMED_REASON in reasons
            )
            if confirmed and not confirmation_valid:
                notes.append("확인 플래그와 3회 연속 확인 횟수/reason/측정 유효성이 상충한다. 원점수는 보존하되 확인된 패턴으로 overlap을 만들지 않는다.")
            expected = 3 if confirmed else min(2, auxiliary_score)
            if confirmation_valid:
                notes.append("6개 고정값이 동일 Pawn에서 3회 연속 확인된 생산자 보고다. 이후 동일 상태의 3점도 새 사건 증분이 아니다.")
            elif pattern:
                notes.append("고정값 패턴 확인 대기다. 1·2회 일치를 확인된 핵심 3점으로 승격하지 않는다.")
            expected_reasons = {
                CONFIRMED_REASON: bool(confirmed), PENDING_REASON: bool(pattern and not confirmed),
                "Injected Module Loaded": bool(injected),
                "Viewport VTable Outside Main Image": bool(viewport),
            }
        else:
            expected = 3 if pattern else min(2, auxiliary_score)
            notes.append("과거 단일 일치 형식이다. 기존 raw_score를 보존하지만 3회 연속 확인 증거로 해석하지 않는다.")
            expected_reasons = {
                LEGACY_REASON: bool(pattern), "Injected Module Loaded": bool(injected),
                "Viewport VTable Outside Main Image": bool(viewport),
            }
        if event["raw_score"] != expected:
            notes.append("원점수가 생산자 점수식과 다르다. 생산자/버전 차이를 확인하고 원점수를 보존한다.")
        if any((reason in reasons) != enabled for reason, enabled in expected_reasons.items()):
            notes.append("플래그와 알려진 reason 조합이 상충한다. reason 또는 점수를 고쳐 쓰지 않는다.")
        if injected:
            notes.append("injected_module은 meccha.dll 이름의 로드 관측이다. 파일 해시/서명으로 악성을 확정하지 않는다.")
        if viewport:
            notes.append("viewport_hook은 Viewport vtable이 메인 이미지 밖이라는 보조 근거다. 특정 ESP 사용을 단정하지 않는다.")
        if pattern == injected == viewport == 0:
            notes.append("0/0/0은 이번 관측 범위의 무일치다. 게임 전체의 정상 보장은 아니다.")
        eligible = baseline.state != "OUT_OF_AUDITED_RANGE" and event["raw_score"] > 0
        if eligible and evidence.get("measurement_valid") is True:
            if (_flag(injected) and injected == 1 and evidence.get("module_observation_valid") is True
                    and "Injected Module Loaded" in reasons):
                tags.append(HIDE_INJECTION)
            if confirmation_valid:
                tags.append(HIDE_VALUE_TAMPER)

    if evidence.get("observation_status") == "unavailable":
        notes.append("모듈/Viewport 일부 관측이 불가하다. 남은 유효 패턴 또는 보조 점수를 보존하되 전체 정상으로 확대하지 않는다.")
    if reasons - _KNOWN_REASONS:
        notes.append("미분류 reason이 포함된다. 자유 문자열로 새 overlap tag를 생성하지 않는다.")
    if event["raw_score"] > 0 and not reasons:
        notes.append("양수에 reason 코드가 없다. 원점수를 보존하지만 원인을 추정하지 않는다.")
    if baseline.state == "OUT_OF_AUDITED_RANGE":
        notes.append("조사 상한 3점을 벗어났다. 점수를 자르거나 overlap을 만들지 않는다.")
    return PolicyAnnotations(overlap_tags=tuple(tags), notes=tuple(notes))
