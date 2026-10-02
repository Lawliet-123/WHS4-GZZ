"""LocalGuard 관측 의미만 추가하는 A 담당 정책. 원점수/저장/판정은 변경하지 않음."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import ntpath
from typing import Any

from ..policy import SignalPreview
from .contract import PolicyAnnotations


SUPPORTED_MODULES = ("external_access",)


def _valid_pid(value: Any) -> bool:
    return type(value) is int and 0 < value <= 0xFFFFFFFF


def _module_scope(evidence: Mapping[str, Any]) -> str | None:
    """게임 PID와 절대 Windows DLL 경로의 관측 범위. 파일/네트워크 접근 없음.

    해시는 경로 문자열의 길이 제한용이지 파일 무결성 해시가 아니다.
    change_type/시각은 키에 넣지 않음: 사건 ID나 재전송 중복 키가 아님.
    """
    pid, path = evidence.get("target_pid"), evidence.get("module_path")
    if not _valid_pid(pid) or not isinstance(path, str) or not path.strip():
        return None
    value = path.strip().replace("/", "\\")
    lowered = value.casefold()
    if lowered.startswith("\\\\?\\unc\\"):
        value = "\\\\" + value[8:]
    elif lowered.startswith("\\\\?\\"):
        value = value[4:]
    elif lowered.startswith("\\??\\"):
        value = value[4:]
    # 상대 경로나 드라이브 없는 경로를 서버의 현재 폴더로 보완하지 않는다.
    if "\x00" in value or not ntpath.isabs(value) or not ntpath.splitdrive(value)[0]:
        return None
    normalized = ntpath.normcase(ntpath.normpath(value))
    digest = sha256(normalized.encode("utf-8")).hexdigest()
    return f"game_module:{pid}:{digest}"


def _external_access(event: Mapping[str, Any], notes: list[str]) -> str | None:
    evidence = event["evidence"]
    submodule = evidence.get("submodule")
    notes.append("external_access는 핸들 관측과 DLL 변화의 혼합 스트림이다. entity_key는 SQLite의 module 저장 키를 분리하지 않는다.")
    if submodule is None and "source_pid" in evidence:
        submodule = "external_process"
        notes.append("submodule이 없는 과거 source_pid 형식은 외부 프로세스 관측 범위로만 해석한다. 원본에 submodule을 추가하지 않는다.")

    if submodule == "external_process":
        notes.append("위험 핸들은 쓰기·메모리 조작·스레드 생성 권한의 관측이다. 해당 프로세스가 실제 핵 기능을 실행했다는 증명은 아니다.")
        notes.append("현재 VM_READ 단독은 탐지기에서 점수를 부여하지 않는다. 서버에서 읽기 권한 점수를 새로 만들지 않는다.")
        notes.append("NORMAL 0점은 이번 핸들 검사에 양수 결과가 없다는 뜻이다. 과거 모든 source_pid의 안전·종료를 증명하지 않는다.")
        rights = evidence.get("access_rights")
        if rights == ["PROCESS_VM_READ"] and event["raw_score"] > 0:
            notes.append("VM_READ 단독 양수는 조사한 탐지기 계약과 다르다. 원점수를 보존하고 전송 버전·근거를 확인한다.")
        if evidence.get("signature_status") in ("unsigned", "unknown", "invalid"):
            notes.append("서명·경로 신뢰 정보는 위험 핸들에 결합되는 보조 근거다. 미서명/조회 실패만으로 치트를 확정하지 않는다.")
        pid = evidence.get("source_pid")
        if event["raw_score"] > 0 and _valid_pid(pid):
            notes.append("source_pid는 외부 접근 주체의 관측 범위다. PID 재사용·다중 핸들·재시작을 사건 ID와 구분한다.")
            return f"external_process:{pid}"
        return None

    if submodule == "module_integrity":
        notes.append("module_integrity는 PR #78의 DLL 추가·매핑 변경·초기 기준선 감사 채널이다. 핸들 접근 신호가 아니다.")
        notes.append("후속 NORMAL 0점은 새 의심 변화가 없다는 뜻이다. 이전 DLL의 제거·무해함 또는 모든 과거 변화의 해소를 뜻하지 않는다.")
        notes.append("현재 module-only 최신 상태는 이 하위 채널과 핸들 관측을 서로 덮어쓸 수 있다. 저장 분리·변화 이력은 B와 별도 합의한다.")
        if event["raw_score"] > 3:
            notes.append("DLL 변화 채널의 조사 상한은 3점이다. external_access 공통 상한 10만으로 이 하위 채널의 범위를 검증할 수 없다.")
        change = evidence.get("change_type")
        if change == "baseline_unreviewed":
            notes.append("초기 미검토 DLL 관측이다. 안티치트 시작 후 새로 주입된 DLL로 해석하지 않는다.")
        elif change == "changed":
            notes.append("같은 경로의 base address/image size 변화다. 파일 바이트 변조나 악성 인라인 패치를 자동 확정하지 않는다.")
        elif change == "added":
            notes.append("직전 성공 기준선 대비 DLL 추가 관측이다. 단독으로 악성 주입을 확정하지 않는다.")
        elif event["raw_score"] > 0:
            notes.append("양수 DLL 결과의 change_type이 없거나 미분류다. 변화 종류를 추정하지 않는다.")
            return None
        if evidence.get("inspection_error") or evidence.get("signature_status") == "unknown":
            notes.append("파일 신뢰 조회 실패와 DLL 매핑 관측은 구분한다. 조회 실패로 서명/파일 해시를 확정하지 않는다.")
        if event["raw_score"] > 0:
            key = _module_scope(evidence)
            if key is None:
                notes.append("유효한 target_pid·절대 DLL 경로가 없어 관측 범위 키를 만들지 않는다. DLL 이름·주소·첫 번째 다른 근거로 대신하지 않는다.")
            else:
                notes.append("game_module 키는 PID+정규화 경로의 범위다. 경로 해시는 파일 해시가 아니며 개별 로드 사건·계정·재전송 ID도 아니다.")
            return key
        return None

    notes.append("submodule이 없거나 미분류다. 핸들 접근/DLL 변경 중 어느 채널인지 임의로 선택하지 않는다.")
    return None


def evaluate(event: Mapping[str, Any], baseline: SignalPreview) -> PolicyAnnotations:
    """Registry가 7필드 검증·baseline 분석을 완료한 뒤 호출하는 읽기 전용 함수."""
    module = event["module"]
    if module not in SUPPORTED_MODULES:
        raise ValueError("unsupported LocalGuard module")
    if baseline.module != module:
        raise ValueError("baseline module does not match the event")
    notes = [
        "이 정책은 원점수·상태를 보존하며 합산·가중치·최종 판정·자동 초기화를 수행하지 않는다.",
        "overlap_tags는 B와 이름·적용 조건을 합의하기 전까지 비워 둔다. 같은 PID/경로만으로 중복 사건을 확정하지 않는다.",
    ]
    if baseline.state == "MEASUREMENT_UNAVAILABLE":
        notes.append("ERROR/OFFLINE/measurement_valid=false는 정상 0점이나 과거 위험 해소가 아니다. 남은 근거로 entity/tag를 만들지 않는다.")
        return PolicyAnnotations(notes=tuple(notes))
    evidence = event["evidence"]
    if evidence.get("status") == "WARNING" or evidence.get("coverage_complete") is False:
        notes.append("부분 검사다. 확인된 근거는 보존하되 전체 정상으로 확대하지 않는다.")
    if baseline.state == "OUT_OF_AUDITED_RANGE":
        notes.append("공통 조사 상한을 벗어난 입력이다. 원점수를 자르지 않고 탐지기 버전·점수 계약을 확인한다.")
    if event["raw_score"] == 0 and evidence.get("status") != "NORMAL" and evidence.get("measurement_valid") is not True:
        notes.append("0점만으로 검사 성공/NORMAL을 추정하지 않는다.")
    key = _external_access(event, notes)
    return PolicyAnnotations(entity_key=key, notes=tuple(notes))
