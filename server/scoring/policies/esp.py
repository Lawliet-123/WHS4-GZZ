"""PR #79 ESP의 개별 근거 스트림을 해석하는 읽기 전용 정책."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import ntpath
from typing import Any

from ..policy import SignalPreview
from .contract import PolicyAnnotations


_CATEGORIES = frozenset(("process_tamper", "memory_read", "handle_duplicate", "overlay", "behavioral_signal"))
_EVENT_CATEGORIES = {
    "process_access": frozenset(("process_tamper", "memory_read", "handle_duplicate")),
    "window_overlap": frozenset(("overlay",)),
    "module_added": frozenset(("behavioral_signal",)),
    "module_changed": frozenset(("behavioral_signal",)),
    "module_trust": frozenset(("behavioral_signal",)),
}
_CATEGORY_NOTES = {
    "process_tamper": "메모리 쓰기·조작·원격 스레드 권한 관측이다. 실제 메모리 변조·ESP 활성화를 직접 증명하지 않는다.",
    "memory_read": "게임 메모리 읽기 권한 관측이다. LocalGuard 핸들 채널의 VM_READ 무점수 계약과 다르며 팀 탐지기 자기 탐지 여부도 검증해야 한다.",
    "handle_duplicate": "핸들 복제 권한 관측이다. 실제 핸들 전송·메모리 읽기·ESP 사용을 확정하지 않는다.",
    "overlay": "게임과 겹치는 외부 창의 스타일·기하 관측이다. 정상 오버레이도 가능하며 DX 함수 후킹·ESP 화면 표시와 동일한 근거가 아니다.",
    "behavioral_signal": "DLL 변화/신뢰의 보조 근거다. 이 이름을 게임 내 비정상 행동을 관측했다는 뜻으로 사용하지 않는다.",
}


def _integer(value: Any) -> int | None:
    """센서가 허용하는 int/숫자 문자열만 읽음. 임의 평문에서 PID를 추출하지 않음."""
    if type(value) is int:
        return value
    if isinstance(value, str) and len(value.strip()) <= 64:
        try:
            return int(value.strip(), 0)
        except ValueError:
            pass
    return None


def _pid(value: Any) -> int | None:
    result = _integer(value)
    return result if result is not None and 0 < result <= 0xFFFFFFFF else None


def _first_pid(evidence: Mapping[str, Any], *names: str) -> int | None:
    for name in names:
        pid = _pid(evidence.get(name))
        if pid is not None:
            return pid
    return None


def _module_scope(evidence: Mapping[str, Any], event_type: str) -> str | None:
    """PID+절대 DLL 경로의 관측 범위. 해시는 파일 바이트가 아니라 경로의 해시."""
    pid = _pid(evidence.get("target_pid"))
    preferred = ("after", "module") if event_type == "module_changed" else ("module",)
    container = evidence
    for name in preferred:
        if isinstance(evidence.get(name), Mapping):
            container = evidence[name]
            break
    path = next((container[name].strip() for name in ("path", "module_path", "image_path")
                 if isinstance(container.get(name), str) and container[name].strip()), None)
    if pid is None or path is None:
        return None
    value = path.replace("/", "\\")
    lowered = value.casefold()
    if lowered.startswith("\\\\?\\unc\\"):
        value = "\\\\" + value[8:]
    elif lowered.startswith("\\\\?\\"):
        value = value[4:]
    elif lowered.startswith("\\??\\"):
        value = value[4:]
    if "\x00" in value or not ntpath.isabs(value) or not ntpath.splitdrive(value)[0]:
        return None
    digest = sha256(ntpath.normcase(ntpath.normpath(value)).encode("utf-8")).hexdigest()
    return f"game_module:{pid}:{digest}"


def evaluate(event: Mapping[str, Any], baseline: SignalPreview) -> PolicyAnnotations:
    """Return read-only evidence annotations; preserve the existing baseline."""
    if event["module"] != "esp":
        raise ValueError("ESP policy supports only esp")
    if baseline.module != event["module"]:
        raise ValueError("baseline module does not match the event")
    notes = [
        "ESP 중앙 Event는 개별 근거의 raw 점수다. 로컬 SuspicionEngine의 0~100 의심도·감쇠·범주 상한과 같은 점수가 아니다.",
        "현재 탐지기 한 관측은 1/2/3점이며 서로 다른 관측의 이력을 최신 모듈 1건으로 복원할 수 없다. Godmode의 event_delta 계약으로 자동 변경하지 않는다.",
        "정상/건강한 빈 배치에는 탐지 Event가 없다. 무전송을 정상 0점·센서 정상·이전 위험 해소로 해석하지 않는다.",
        "overlap_tags는 B와 공통 이름·조건을 합의하기 전까지 비워 둔다. 동일 PID/경로·유사 범주만으로 LocalGuard와 중복을 확정하지 않는다.",
        "sensor_event_id와 Shared 전송 event_id는 다른 식별자다. 중앙 재전송 중복은 Shared ID를 사용하며 이 정책에서 ID를 다시 만들지 않는다.",
    ]
    if baseline.state == "POLICY_NOT_CALIBRATED":
        notes.append(
            "공통 ESP 프로필은 positive_only 근거 스트림으로 검토됐지만 "
            "최종 위험도 보정·가중치·판정 기준은 아직 확정되지 않았다."
        )
    elif baseline.state in ("AWAITING_DETECTOR", "UNKNOWN_MODULE"):
        notes.append(
            "공통 ESP 프로필이 아직 미검토 상태다. 이 정책은 baseline을 "
            "정상·보정 완료 상태로 바꾸지 않는다."
        )

    evidence = event["evidence"]
    # 명시적 실패 상태는 reviewed profile에서도 정상 근거보다 우선한다.
    # annotations는 SignalPreview를 변경하지 않고 실패 의미만 설명한다.
    if baseline.state == "MEASUREMENT_UNAVAILABLE" or evidence.get("status") in ("ERROR", "OFFLINE") or evidence.get("measurement_valid") is False:
        notes.append("명시적 실패/오프라인/측정 무효다. 정상 근거로 해석하지 않고 entity/tag를 만들지 않는다.")
        return PolicyAnnotations(notes=tuple(notes))
    if evidence.get("status") == "WARNING" or evidence.get("coverage_complete") is False:
        notes.append("부분 검사다. 확보된 근거를 보존하되 전체 정상으로 확대하지 않는다.")
    sensor_id = evidence.get("sensor_event_id")
    if not isinstance(sensor_id, str) or not sensor_id.strip():
        notes.append("sensor_event_id가 없다. 로그 회차·시각·PID로 개별 관측 ID를 임의 복원하지 않는다.")
    if event["raw_score"] > 3:
        notes.append("현재 EspEventDetector의 단일 관측 상한 3점을 벗어났다. 어댑터는 여러 연결 근거를 더할 수 있으므로 확장/버전 계약을 확인하고 원점수는 보존한다.")
    if event["raw_score"] == 0:
        notes.append("0점 Event는 현재 중앙 생산자의 양수 근거 전송 계약과 다르다. 전체 정상 보고로 사용하지 않는다.")
        return PolicyAnnotations(notes=tuple(notes))

    event_type, categories = evidence.get("event_type"), evidence.get("categories")
    if not isinstance(event_type, str) or event_type not in _EVENT_CATEGORIES:
        notes.append("event_type이 없거나 미분류다. reasons/파일 이름에서 센서 종류를 추정하지 않는다.")
        return PolicyAnnotations(notes=tuple(notes))
    if not isinstance(categories, list) or not categories or not all(isinstance(item, str) and item in _CATEGORIES for item in categories):
        notes.append("categories가 없거나 형식/분류가 다르다. 알려진 범주로 자동 보완하거나 자유 문자열 태그를 만들지 않는다.")
        return PolicyAnnotations(notes=tuple(notes))
    unique = set(categories)
    for category in sorted(unique):
        notes.append(_CATEGORY_NOTES[category])
    if len(categories) != len(unique):
        notes.append("중복 category가 있다. 목록 길이를 새로운 근거 수로 곱하지 않는다.")
    if len(unique) != 1 or not unique.issubset(_EVENT_CATEGORIES[event_type]):
        notes.append("현재 단일 관측 탐지기의 event_type/category 계약과 다르다. 여러 대상/근거를 하나의 entity로 축약하지 않는다.")
        return PolicyAnnotations(notes=tuple(notes))
    category = next(iter(unique))
    expected = {"process_tamper": 3, "memory_read": 2, "handle_duplicate": 1, "overlay": 1}.get(category)
    if category == "behavioral_signal":
        expected = 3 if "module hash matched the configured known-bad list" in event["reasons"] else 1
    if event["raw_score"] != expected:
        notes.append("현재 알려진 관측의 점수 생성식과 다르다. 원점수를 보존하고 reason·생산자 버전을 확인한다.")

    key = None
    if event_type == "process_access":
        source = _first_pid(evidence, "source_pid", "source_process_id")
        target = _first_pid(evidence, "target_pid", "target_process_id")
        if source is not None and source != target:
            key = f"external_process:{source}"
            notes.append("external_process는 접근 주체 PID 범위다. 계정·개별 핸들 사건이 아니며 PID 재사용에 유의한다.")
        else:
            notes.append("유효한 외부 source_pid가 없다. 게임 PID·파일 경로에서 외부 접근 주체를 임의로 만들지 않는다.")
    elif event_type == "window_overlap":
        pid = _first_pid(evidence, "window_pid", "candidate_pid")
        game = _first_pid(evidence, "game_pid", "target_pid")
        hwnd = _integer(evidence.get("hwnd"))
        if pid not in (None, 4) and pid != game and hwnd is not None and 0 < hwnd < 2**64:
            key = f"overlay_window:{pid}:{hwnd}"
            notes.append("overlay_window는 외부 창 범위다. HWND/PID 재사용이 가능하며 DX 후킹 주소·개별 ESP 실행 사건과 같은 키가 아니다.")
        else:
            notes.append("유효한 외부 창 PID/HWND가 없어 범위 키를 만들지 않는다. 창 제목·겹침 비율을 ID로 대신하지 않는다.")
    else:
        key = _module_scope(evidence, event_type)
        notes.append("game_module 키는 게임 PID+정규화 DLL 경로의 범위이며 경로 해시는 파일 바이트 해시/개별 로드 사건 ID가 아니다.")
        if key is None:
            notes.append("유효한 게임 PID·절대 DLL 경로가 없다. DLL 이름·파일 해시·서명 상태를 대상 범위의 대체 ID로 사용하지 않는다.")
        if event_type == "module_added":
            notes.append("직전 성공 기준선 뒤 DLL 추가 관측이다. 정상 로드도 가능하며 단독으로 악성 주입을 확정하지 않는다.")
        elif event_type == "module_changed":
            notes.append("DLL load metadata 변화 관측이다. 파일 바이트 패치나 코드 무결성 실패의 증명은 아니다.")
        else:
            notes.append("서명 미확인/신뢰 실패/알려진 해시 일치를 구분한다. known-bad 카탈로그 일치도 현재 ESP 행동의 증명은 아니다.")
        if evidence.get("baseline_created") is True or evidence.get("observation_phase") == "baseline":
            notes.append("초기 기준선 관측이다. 검사 시작 후 새로 주입됐다고 해석하지 않는다. 현재 생산자는 초기 미서명만으로 양수 근거를 생성하지 않는다.")
    return PolicyAnnotations(entity_key=key, notes=tuple(notes))
