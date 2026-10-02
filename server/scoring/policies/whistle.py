"""Whistle의 서로 다른 관측 경로를 해석하는 읽기 전용 A 담당 정책.

원시 점수, 측정 상태, 저장 키, 가중치와 최종 판정을 변경하지 않는다.
overlap_tags는 B와 공통 이름을 합의하기 전까지 비워 둔다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..policy import SignalPreview
from .contract import PolicyAnnotations


SUPPORTED_MODULES = ("whistle", "whistle_rpc")

_STATIC_REASONS = {
    "exec_function_hooked": "ExecFunction 교체 흔적이며 RPC 위반 호출 자체를 관측한 것은 아니다.",
    "character_vtable_hooked": "캐릭터 ProcessEvent vtable 교체 흔적이며 RPC 위반 호출 자체를 관측한 것은 아니다.",
    "provocation_sound_swapped": "도발 사운드 교체 근거는 보조 관측이며 단독으로 RPC 위반을 확정하지 않는다.",
}
_RPC_REASONS = {
    "role_violation": "RPC 호출자 역할 위반 관측이다.",
    "dead_caller": "RPC 호출자의 생존 상태 위반 관측이다.",
    "foreign_target": "RPC 대상 지정 위반 관측이다.",
    "cooldown_violation": (
        "쿨다운 위반은 호출 빈도 관측이다. 0.60초는 탐지기 설정값이며 게임의 공식 쿨다운으로 확정하지 않는다."
    ),
    "no_input_event": "탐지기 기준 입력 이벤트 없이 RPC가 호출된 관측이며 화면 회전 입력 탐지와 같은 신호는 아니다.",
}


def _nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def evaluate(event: Mapping[str, Any], baseline: SignalPreview) -> PolicyAnnotations:
    """Shared 7필드 이벤트에 관측 범위와 해석 한계만 추가한다.

    Registry가 Shared 검증과 inspect_event()를 먼저 수행한다. entity_key의
    game_pid는 검사 대상 게임 프로세스 범위일 뿐, 핵 프로세스/계정/개별 RPC
    사건의 식별자가 아니며 SQLite 최신 상태를 분리하는 저장 키도 아니다.
    """
    module = event["module"]
    if module not in SUPPORTED_MODULES:
        raise ValueError("whistle policy supports only whistle and whistle_rpc")
    if baseline.module != module:
        raise ValueError("baseline module does not match the event")

    evidence = event["evidence"]
    reasons = set(event["reasons"])
    meta = evidence.get("meta")
    meta = meta if isinstance(meta, Mapping) else {}
    notes = [
        "현재 클라이언트 구현은 로컬에 0점도 기록하지만 중앙에는 양수만 전송한다.",
        "새 이벤트가 없다는 사실을 정상 0점이나 검사 성공으로 해석하지 않는다. 유지·만료 기준은 B와 합의가 필요하다.",
        "후킹 흔적과 RPC 호출 위반은 다른 관측이다. 두 채널의 점수를 같은 사건 또는 독립 사건으로 단정하여 합산하지 않는다.",
        "overlap_tags는 B와 이름·적용 조건을 합의하기 전까지 비워 둔다. 후보 주석 자체로 중복을 제거하거나 감점하지 않는다.",
    ]

    # 실패한 관측은 남아 있는 reasons/PID가 있어도 유효한 근거로 승격하지 않는다.
    if baseline.state == "MEASUREMENT_UNAVAILABLE":
        notes.append("ERROR/OFFLINE/측정 무효는 정상 0점 또는 이전 위험이 해소된 관측으로 사용하지 않는다.")
        return PolicyAnnotations(notes=tuple(notes))

    if evidence.get("status") == "WARNING" or evidence.get("coverage_complete") is False:
        notes.append("검사 범위가 부분적이므로 이번 결과만으로 전체 정상 여부를 확정하지 않는다.")
    if baseline.state == "OUT_OF_AUDITED_RANGE":
        notes.append("조사한 점수 상한을 벗어난 입력이다. 원점수를 자르거나 정상화하지 않고 탐지기 버전을 재확인한다.")
    if event["raw_score"] == 0:
        if evidence.get("status") == "NORMAL":
            notes.append("명시적 NORMAL 0점은 이번 관측 범위에서 위반 근거가 없다는 뜻이며 전체 무결성 보장이 아니다.")
        else:
            notes.append("0점만 있고 성공 상태가 명시되지 않았다. NORMAL로 자동 보완하지 않는다.")

    entity_key = None
    if module == "whistle":
        notes.append("whistle은 스캔 시점의 후킹·사운드 상태 관측이다. 반복 점수는 신규 사건 수가 아니므로 누적 가산하지 않는다.")
        target_pid = meta.get("target_pid")
        if event["raw_score"] > 0 and type(target_pid) is int and 0 < target_pid <= 0xFFFFFFFF:
            entity_key = f"game_pid:{target_pid}"
            notes.append("game_pid는 검사 대상 게임 프로세스 범위다. PID 재사용과 여러 함수·주소 관측을 개별 사건 식별로 해석하지 않는다.")
        for reason, note in _STATIC_REASONS.items():
            if reason in reasons:
                notes.append(note)
        if "address" in evidence:
            notes.append("address/value는 설명을 포함한 평문일 수 있어 파싱으로 함수·주소별 사건 키를 만들지 않는다.")
    else:
        notes.append("whistle_rpc의 점수는 해당 관측 범위에 나타난 위반 코드별 점수를 합쳐 상한을 적용한 값이다. 같은 코드의 호출 횟수를 다시 곱하지 않는다.")
        if meta.get("mode") == "window":
            notes.append("window 모드는 새 로그 구간 관측이다. raw_score는 사건별 증분 계약이 아니며 최신 한 건만으로 과거 구간 이력을 복원할 수 없다.")
            calls = meta.get("window_calls")
            if _nonnegative_int(calls):
                if calls == 0:
                    notes.append("이번 구간의 도발 호출은 0건이다. 호출 행동을 검증한 정상 표본과 구분하며 이 정책은 후크 생존을 독립 검증하지 않는다.")
                else:
                    notes.append("window_calls는 이번 구간의 호출 관측 수다. Server/Client 경로의 호출 수를 사용자 입력 횟수나 치트 사건 수로 치환하지 않는다.")
            else:
                notes.append("window_calls가 없거나 유효한 정수가 아니므로 이번 구간의 호출 유무를 확정하지 않는다.")
        elif "provocation_calls" in meta or "violation_records" in meta:
            notes.append("단발·과거 측정 형식은 후크 로그 전체를 평가할 수 있다. 동일 로그의 반복 결과를 새로운 사건으로 가산하지 않는다.")
        else:
            notes.append("RPC 관측 모드와 로그 구간 정보가 부족하다. 신규 구간/전체 로그 또는 개별 사건으로 추정하지 않는다.")
        if _nonnegative_int(meta.get("log_restarts")) and meta["log_restarts"] > 0:
            notes.append("후크 로그 축소·재시작이 보고되었다. 재시작 전후 커서와 사건 식별의 연속성을 별도로 확인한다.")
        for reason, note in _RPC_REASONS.items():
            if reason in reasons:
                notes.append(note)
        notes.append("RPC Event에는 안정된 호출자/개별 사건 키가 없다. 로그 경로·회차·누적 호출 수로 entity_key를 만들지 않는다.")

    known_reasons = _STATIC_REASONS if module == "whistle" else _RPC_REASONS
    if reasons - known_reasons.keys():
        notes.append("미분류 reason이 포함되어 있다. 자유 문자열로 새 중복 태그를 생성하거나 원점수를 재계산하지 않는다.")
    if event["raw_score"] > 0 and not reasons:
        notes.append("양수 점수에 reason 코드가 없다. 원점수는 보존하지만 원인과 중복 후보는 추정하지 않는다.")

    return PolicyAnnotations(entity_key=entity_key, notes=tuple(notes))
