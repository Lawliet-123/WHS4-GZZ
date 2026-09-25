"""Windows process handle 접근 권한을 사람이 읽을 수 있는 근거로 바꾼다."""

from typing import Dict, List

# winnt.h PROCESS_* access-right constants.  PROCESS_ALL_ACCESS처럼 여러 비트가
# 섞인 경우도 아래 마스크 검사로 개별 위험 권한을 모두 보존한다.
PROCESS_CREATE_THREAD = 0x0002
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020

RIGHT_NAMES: Dict[int, str] = {
    PROCESS_VM_READ: "PROCESS_VM_READ",
    PROCESS_VM_WRITE: "PROCESS_VM_WRITE",
    PROCESS_VM_OPERATION: "PROCESS_VM_OPERATION",
    PROCESS_CREATE_THREAD: "PROCESS_CREATE_THREAD",
}

# 읽기만으로는 오탐 가능성이 높아 v1 기본 정책에서 점수를 주지 않는다.
RISK_WEIGHTS: Dict[int, int] = {
    PROCESS_VM_WRITE: 2,
    PROCESS_VM_OPERATION: 2,
    PROCESS_CREATE_THREAD: 3,
}


def describe_access_mask(access_mask: int) -> List[str]:
    """관심 권한 비트만 안정된 순서의 이름 목록으로 반환한다."""
    return [name for bit, name in RIGHT_NAMES.items() if access_mask & bit]


def risky_access_score(access_mask: int) -> int:
    """메모리 쓰기·조작·원격 스레드 권한의 합산 점수다.

    최종 차단 임계값이 아니라 LocalGuard가 중앙 판정기에 보내는 raw_score의
    일부다. 점수 보정은 정상/핵 실험 로그가 쌓인 뒤에 한다.
    """
    return sum(weight for bit, weight in RISK_WEIGHTS.items() if access_mask & bit)


def has_risky_access(access_mask: int) -> bool:
    return risky_access_score(access_mask) > 0
