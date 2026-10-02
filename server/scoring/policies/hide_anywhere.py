"""Hide Anywhere v9의 점수/관측 한계를 설명하는 읽기 전용 정책."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..policy import SignalPreview
from .contract import PolicyAnnotations


_FLAGS = ("hide_value_pattern", "injected_module", "viewport_hook")
_REASONS = {
    "Hide Anywhere Value Pattern Matched": "hide_value_pattern",
    "Injected Module Loaded": "injected_module",
    "Viewport VTable Outside Main Image": "viewport_hook",
}


def evaluate(event: Mapping[str, Any], baseline: SignalPreview) -> PolicyAnnotations:
    """점수 재계산·상태 교체·관측 이력·실제 메모리 검사는 하지 않는다."""
    if event["module"] != "hide_anywhere":
        raise ValueError("Hide Anywhere policy supports only hide_anywhere")
    if baseline.module != event["module"]:
        raise ValueError("baseline module does not match the event")
    notes = [
        "현재 v9는 매 관측 시점의 snapshot이다. 반복 양수를 새 사건 수로 합산하거나 정상/오류 결과로 이전 위험을 자동 해제하지 않는다.",
        "공통 Event에는 안정된 대상 PID/Pawn·개별 사건 ID가 없다. session/player/로그 시각으로 가짜 entity_key를 만들지 않는다.",
        "overlap_tags는 B와 공통 이름·조건을 합의하기 전까지 비워 둔다. DLL/후킹 보조 근거를 LocalGuard와 자동 중복 확정하지 않는다.",
    ]
    if baseline.state == "MEASUREMENT_UNAVAILABLE":
        notes.append("ERROR/OFFLINE/measurement_valid=false는 정상 0점이 아니다. 남아 있는 플래그·점수로 유효 관측을 복구하지 않는다.")
        return PolicyAnnotations(notes=tuple(notes))

    evidence = event["evidence"]
    reasons = set(event["reasons"])
    if evidence.get("status") == "WARNING" or evidence.get("coverage_complete") is False:
        notes.append("부분 검사다. 확보된 근거는 보존하되 전체 정상으로 확대하지 않는다.")
    if evidence.get("measurement_valid") is not True:
        notes.append("측정 유효성이 명시되지 않았다. 현재 3개 플래그만으로 읽기 실패·누락·최신 값 여부를 정상 미일치와 구분할 수 없다.")
        notes.append("원본 로거는 필드 읽기 실패 뒤 이전 성공 값을 유지할 수 있다. 중앙에서 원시 읽기 로그 없이 이번 값이 새로 읽혔다고 확정하지 않는다.")
    else:
        notes.append("measurement_valid=true는 생산자의 명시적 보고다. 중앙 정책이 실제 메모리 읽기·값의 신선도를 독립 검증한 것은 아니다.")

    valid_flags = all(type(evidence.get(name)) is int and evidence[name] in (0, 1) for name in _FLAGS)
    if not valid_flags:
        notes.append("v9의 0/1 정수 플래그가 누락되거나 형식이 다르다. 없는 값을 0으로 보완하거나 bool/문자열을 자동 변환하지 않는다.")
    else:
        pattern, injected, viewport = (evidence[name] for name in _FLAGS)
        # 생성식과 비교해 계약 차이를 설명할 뿐, 원점수는 반환값에서 교체하지 않는다.
        expected = 3 if pattern else min(2, injected + viewport)
        if event["raw_score"] != expected:
            notes.append("원점수가 현재 v9 생성식(패턴 3점, 아니면 보조 근거 최대 2점)과 다르다. 구버전/계약 차이를 확인하고 원점수는 보존한다.")
        if pattern:
            notes.append("6개 고정 설정값의 패턴 일치 보고다. 실제 숨기 성공·거리 무시 행동까지 직접 관측한 것은 아니다.")
        if injected:
            notes.append("injected_module은 이름이 meccha.dll인 로드 모듈 관측이다. 파일 해시/서명·악성 주입 주체를 증명하지 않는다.")
        if viewport:
            notes.append("viewport_hook은 후보 Viewport vtable 슬롯이 메인 이미지 밖이라는 관측이다. 실제 렌더링 후킹 동작·특정 ESP 사용과 구분한다.")
        if pattern == injected == viewport == 0:
            notes.append("0/0/0은 이번에 보고된 패턴·보조 근거가 없다는 뜻이다. 성공한 최신 메모리 검사 또는 전체 무결성의 증명은 아니다.")
        if any((flag in reasons) != bool(evidence[key]) for flag, key in _REASONS.items()):
            notes.append("플래그와 알려진 reason 목록이 현재 v9 생성 계약과 다르다. 서로 대신 채우거나 점수를 새로 만들지 않는다.")

    notes.append("Rule 클래스의 3회 확인과 실제 make_common_event 경로는 다르다. 현재 3점은 패턴 1회 일치에도 나오며 3회 연속 확인 증명이 아니다.")
    if reasons - _REASONS.keys():
        notes.append("미분류 reason이 포함된다. 자유 문자열로 핵 종류·새 중복 태그를 생성하지 않는다.")
    if event["raw_score"] > 0 and not reasons:
        notes.append("양수 점수에 reason이 없다. 원점수를 보존하되 관측 원인을 추정하지 않는다.")
    if baseline.state == "OUT_OF_AUDITED_RANGE":
        notes.append("조사 상한 3점을 벗어난 입력이다. 원점수를 자르거나 최종 위험도로 바꾸지 않는다.")
    return PolicyAnnotations(notes=tuple(notes))
