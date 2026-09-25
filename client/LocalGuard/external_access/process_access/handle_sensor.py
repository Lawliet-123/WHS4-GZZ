"""게임 프로세스를 가리키는 외부 process handle을 읽는 Windows 센서.

NtQuerySystemInformation(SystemExtendedHandleInformation)은 Windows 내부 API다.
따라서 호출 실패·구조 변경·권한 부족을 정상적인 수집 불가 상태로 취급하고,
이 파일 외부에 Windows 내부 구조를 퍼뜨리지 않는다.
"""

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence

from ..common.process_locator import describe_process
from ..common.models import TargetProcess
from .access_rights import describe_access_mask
from .models import ExternalHandleObservation

SYSTEM_EXTENDED_HANDLE_INFORMATION = 64
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_DUP_HANDLE = 0x0040
DUPLICATE_SAME_ACCESS = 0x00000002
# PID 0은 Idle, PID 4는 Windows kernel System process다. 실행 파일 artifact가
# 없고 운영체제 자체가 프로세스 수명주기 관리를 위해 보유하는 handle이므로,
# 사용자 영역의 외부 접근 주체로 보고하지 않는다.
KERNEL_PROCESS_IDS = frozenset({0, 4})


class HandleSensorUnavailable(RuntimeError):
    """권한·운영체제 차이 등으로 시스템 핸들을 안전하게 수집할 수 없을 때 발생."""


@dataclass(frozen=True)
class SystemHandleEntry:
    """SystemExtendedHandleInformation의 필요한 필드만 옮긴 값 객체."""

    owner_pid: int
    handle_value: int
    object_address: int
    granted_access: int
    object_type_index: int = 0


@dataclass(frozen=True)
class _TargetHandleIdentity:
    """우리 query handle에서 확인한 process object 식별 정보."""

    object_address: Optional[int]
    object_type_index: int


class _SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("Object", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_size_t),
        ("HandleValue", ctypes.c_size_t),
        ("GrantedAccess", wintypes.ULONG),
        ("CreatorBackTraceIndex", wintypes.USHORT),
        ("ObjectTypeIndex", wintypes.USHORT),
        ("HandleAttributes", wintypes.ULONG),
        ("Reserved", wintypes.ULONG),
    ]


EntryProvider = Callable[[], Iterable[SystemHandleEntry]]
ProcessDescriber = Callable[[int], TargetProcess]
TargetIdentityResolver = Callable[[int, Sequence[SystemHandleEntry], int], _TargetHandleIdentity]
HandleTargetResolver = Callable[[SystemHandleEntry, int], bool]


class ExternalHandleSensor:
    """게임 객체와 같은 kernel object를 가리키는 외부 handle을 모은다."""

    def __init__(
        self,
        entry_provider: Optional[EntryProvider] = None,
        process_describer: ProcessDescriber = describe_process,
        current_pid_provider: Callable[[], int] = os.getpid,
        target_identity_resolver: Optional[TargetIdentityResolver] = None,
        handle_target_resolver: Optional[HandleTargetResolver] = None,
    ) -> None:
        self._entry_provider = entry_provider or iter_system_handle_entries
        self._process_describer = process_describer
        self._current_pid_provider = current_pid_provider
        self._target_identity_resolver = target_identity_resolver
        self._handle_target_resolver = handle_target_resolver or handle_targets_process

    def scan(self, game: TargetProcess) -> List[ExternalHandleObservation]:
        """게임을 대상으로 한 외부 process handle을 PID별로 합쳐 반환한다."""
        current_pid = self._current_pid_provider()
        if self._target_identity_resolver is None:
            # 시스템 목록을 읽기 전에 handle을 열어야, 그 handle이 같은 snapshot에
            # 포함돼 object type을 식별할 수 있다.
            with open_query_handle(game.pid) as own_handle_value:
                entries = list(self._entry_provider())
                target_identity = find_target_handle_identity(own_handle_value, entries, current_pid)
        else:
            entries = list(self._entry_provider())
            target_identity = self._target_identity_resolver(game.pid, entries, current_pid)

        access_by_owner = {}
        for entry in entries:
            if entry.owner_pid in KERNEL_PROCESS_IDS or entry.owner_pid in {game.pid, current_pid}:
                continue
            # 관심 권한이 전혀 없는 동기화/조회 handle은 관찰 대상에서 제외한다.
            if not describe_access_mask(entry.granted_access):
                continue
            if target_identity.object_address is not None:
                if entry.object_address != target_identity.object_address:
                    continue
            else:
                # 최근 Windows에서는 kernel object address를 0으로 마스킹할 수 있다.
                # process handle type만 후보로 좁힌 뒤, 복제 가능한 handle에 한해
                # GetProcessId로 실제 대상 PID를 확인한다.
                if entry.object_type_index != target_identity.object_type_index:
                    continue
                if not self._handle_target_resolver(entry, game.pid):
                    continue
            access_by_owner[entry.owner_pid] = access_by_owner.get(entry.owner_pid, 0) | entry.granted_access

        observations = []
        for owner_pid, granted_access in access_by_owner.items():
            source = self._process_describer(owner_pid)
            observations.append(
                ExternalHandleObservation(
                    source_pid=owner_pid,
                    source_name=source.executable_name,
                    source_path=source.executable_path,
                    granted_access=granted_access,
                )
            )
        return sorted(observations, key=lambda item: item.source_pid)


def find_target_handle_identity(
    own_handle_value: int,
    entries: Sequence[SystemHandleEntry],
    current_pid: int,
) -> _TargetHandleIdentity:
    """같은 snapshot 안의 우리 query-only handle에서 게임 object type을 찾는다."""
    for entry in entries:
        if entry.owner_pid == current_pid and entry.handle_value == own_handle_value:
            return _TargetHandleIdentity(
                object_address=entry.object_address or None,
                object_type_index=entry.object_type_index,
            )

    raise HandleSensorUnavailable("게임 query handle의 형식을 시스템 목록에서 찾지 못함")


@contextmanager
def open_query_handle(target_pid: int):
    """게임을 가리키는 query-only handle을 열고, snapshot 뒤 즉시 닫는다."""
    if os.name != "nt":
        raise HandleSensorUnavailable("외부 process handle 센서는 Windows에서만 지원됨")
    kernel32 = _kernel32()
    own_handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, target_pid)
    if not own_handle:
        raise HandleSensorUnavailable(f"게임 PID {target_pid}에 query handle을 열 수 없음")
    try:
        yield int(own_handle)
    finally:
        kernel32.CloseHandle(own_handle)


def handle_targets_process(entry: SystemHandleEntry, target_pid: int) -> bool:
    """복제 가능한 process handle이 실제로 target_pid를 가리키는지 확인한다.

    보호 프로세스나 권한이 다른 프로세스의 handle은 복제하지 못할 수 있다. 그
    경우에는 관찰 불가로 두며, 추측으로 탐지 결과를 만들지 않는다.
    """
    if os.name != "nt":
        return False
    kernel32 = _kernel32()
    source_process = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, entry.owner_pid)
    if not source_process:
        return False

    try:
        # 먼저 조회 전용 권한으로 축소 복제를 시도한다. 일부 handle은 Windows가
        # 권한 축소 복제를 거부하므로, 그때만 원래 권한 그대로 복제해 PID만 읽고
        # 즉시 닫는다. 어느 경우에도 복제 handle로 메모리를 읽거나 쓰지 않는다.
        attempts = (
            (PROCESS_QUERY_LIMITED_INFORMATION, 0),
            (0, DUPLICATE_SAME_ACCESS),
        )
        for desired_access, options in attempts:
            duplicated = wintypes.HANDLE()
            success = kernel32.DuplicateHandle(
                source_process,
                wintypes.HANDLE(entry.handle_value),
                kernel32.GetCurrentProcess(),
                ctypes.byref(duplicated),
                desired_access,
                False,
                options,
            )
            if not success:
                continue
            try:
                duplicated_pid = int(kernel32.GetProcessId(duplicated))
                if duplicated_pid == target_pid:
                    return True
                if duplicated_pid != 0:
                    # PID를 정상 확인했으며 다른 process를 가리키는 handle이다.
                    return False
                # 조회 전용 복제 handle에서 PID 조회가 거부된 경우에는
                # DUPLICATE_SAME_ACCESS 방식으로 한 번 더 확인한다.
            finally:
                kernel32.CloseHandle(duplicated)
        return False
    finally:
        kernel32.CloseHandle(source_process)


def iter_system_handle_entries() -> Iterable[SystemHandleEntry]:
    """NtQuerySystemInformation 결과를 최소 필드로 파싱한다."""
    if os.name != "nt":
        raise HandleSensorUnavailable("외부 process handle 센서는 Windows에서만 지원됨")

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    query = ntdll.NtQuerySystemInformation
    query.argtypes = (
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    )
    query.restype = ctypes.c_long

    buffer_size = 64 * 1024
    for _ in range(8):
        buffer = ctypes.create_string_buffer(buffer_size)
        returned_length = wintypes.ULONG()
        status = query(
            SYSTEM_EXTENDED_HANDLE_INFORMATION,
            ctypes.byref(buffer),
            buffer_size,
            ctypes.byref(returned_length),
        )
        if status == 0:
            break
        if (status & 0xFFFFFFFF) != STATUS_INFO_LENGTH_MISMATCH:
            raise HandleSensorUnavailable(f"NtQuerySystemInformation failed: 0x{status & 0xFFFFFFFF:08X}")
        buffer_size = max(buffer_size * 2, int(returned_length.value) + 4096)
    else:
        raise HandleSensorUnavailable("시스템 handle 목록 버퍼를 확보하지 못함")

    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    header_size = pointer_size * 2  # NumberOfHandles + Reserved
    entry_size = ctypes.sizeof(_SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
    count = ctypes.c_size_t.from_buffer_copy(buffer.raw[:pointer_size]).value
    max_entries = (len(buffer) - header_size) // entry_size
    if count > max_entries:
        raise HandleSensorUnavailable("시스템 handle 목록 구조가 예상 범위를 벗어남")

    base = ctypes.addressof(buffer) + header_size
    for index in range(count):
        raw = _SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX.from_address(base + index * entry_size)
        yield SystemHandleEntry(
            owner_pid=int(raw.UniqueProcessId),
            handle_value=int(raw.HandleValue),
            object_address=int(raw.Object or 0),
            granted_access=int(raw.GrantedAccess),
            object_type_index=int(raw.ObjectTypeIndex),
        )


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = ()
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.DuplicateHandle.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    kernel32.DuplicateHandle.restype = wintypes.BOOL
    kernel32.GetProcessId.argtypes = (wintypes.HANDLE,)
    kernel32.GetProcessId.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32
