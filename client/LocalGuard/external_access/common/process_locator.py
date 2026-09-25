"""게임 PID를 찾아 하위 탐지기들이 같은 프로세스를 보게 한다."""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from .models import TargetProcess

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH = 260


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


class ProcessLocator:
    def __init__(self, game_executable_name: str,
                 enumerator: Optional[Callable[[], Iterable[TargetProcess]]] = None) -> None:
        if not game_executable_name:
            raise ValueError("game_executable_name은 비어 있을 수 없음")
        self.game_executable_name = game_executable_name.casefold()
        self._enumerator = enumerator or iter_windows_processes

    def find(self) -> Optional[TargetProcess]:
        return next((p for p in self._enumerator()
                     if p.executable_name.casefold() == self.game_executable_name), None)

    @staticmethod
    def was_restarted(previous: Optional[TargetProcess], current: Optional[TargetProcess]) -> bool:
        if previous is None or current is None:
            return previous is not current
        return (previous.pid, previous.create_time) != (current.pid, current.create_time)


def describe_process(pid: int) -> TargetProcess:
    """PID 하나의 실행 파일 정보를 best-effort로 조회한다.

    보호 프로세스처럼 경로 조회가 거부되는 경우에도 관찰 자체는 버리지 않고
    ``pid_<숫자>`` 이름과 None 경로를 반환한다.
    """
    path = get_process_image_path(pid)
    return TargetProcess(
        pid=pid,
        executable_name=path.name if path else f"pid_{pid}",
        executable_path=path,
        create_time=get_process_create_time(pid),
    )


def iter_windows_processes() -> Iterator[TargetProcess]:
    if os.name != "nt":
        return
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        has_entry = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while has_entry:
            pid = int(entry.th32ProcessID)
            yield TargetProcess(pid, entry.szExeFile, get_process_image_path(pid), get_process_create_time(pid))
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            has_entry = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)


def get_process_image_path(pid: int) -> Optional[Path]:
    return _query_process(pid, "path")


def get_process_create_time(pid: int) -> Optional[float]:
    return _query_process(pid, "time")


def _query_process(pid: int, kind: str):
    if os.name != "nt":
        return None
    k = _kernel32()
    handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        if kind == "path":
            length = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(length.value)
            return Path(buffer.value) if k.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)) else None
        created = wintypes.FILETIME(); exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME(); user = wintypes.FILETIME()
        if not k.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 10_000_000 - 11_644_473_600
    finally:
        k.CloseHandle(handle)


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD); k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)); k.Process32FirstW.restype = wintypes.BOOL
    k.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)); k.Process32NextW.restype = wintypes.BOOL
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD); k.OpenProcess.restype = wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)); k.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k.GetProcessTimes.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)); k.GetProcessTimes.restype = wintypes.BOOL
    k.CloseHandle.argtypes = (wintypes.HANDLE,); k.CloseHandle.restype = wintypes.BOOL
    return k
