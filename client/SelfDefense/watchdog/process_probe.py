"""Read-only Windows process observations; never spawn, terminate or write memory.

Keep one identity-checked handle per target until its registration changes. This
lets us read an exit code after Launcher releases its Popen handle. Missing an
entire short run still yields unknown, not a fabricated successful completion.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ProcessState:
    status: str
    exit_code: int | None = None
    error_code: str | None = None


class WindowsAPI:
    def __init__(self):
        if os.name != "nt":
            raise OSError("WINDOWS_REQUIRED")
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "OpenProcess": ((wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE),
            "CloseHandle": ((wintypes.HANDLE,), wintypes.BOOL),
            "WaitForSingleObject": ((wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD),
            "GetProcessTimes": ((wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4, wintypes.BOOL),
            "GetExitCodeProcess": ((wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)), wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            method = getattr(self.k32, name)
            method.argtypes, method.restype = args, result

    def open(self, pid):
        ctypes.set_last_error(0)
        handle = self.k32.OpenProcess(0x1000 | 0x100000, False, pid)
        return handle, 0 if handle else ctypes.get_last_error()

    def created(self, handle):
        fields = [wintypes.FILETIME() for _ in range(4)]
        if not self.k32.GetProcessTimes(handle, *(ctypes.byref(x) for x in fields)):
            return None
        return (fields[0].dwHighDateTime << 32) | fields[0].dwLowDateTime

    def wait(self, handle):
        return self.k32.WaitForSingleObject(handle, 0)

    def exit_code(self, handle):
        code = wintypes.DWORD()
        return code.value if self.k32.GetExitCodeProcess(handle, ctypes.byref(code)) else None

    def close(self, handle):
        self.k32.CloseHandle(handle)


class ProcessProbe:
    def __init__(self, api=None):
        self.api = api
        self._handles = {}

    def read(self, name, pid, created):
        if (type(pid) is not int or not 0 < pid <= 0xFFFFFFFF or
                type(created) is not int or not 0 < created <= 0xFFFFFFFFFFFFFFFF):
            return ProcessState("error", error_code="PROCESS_INVALID_IDENTITY")
        if self.api is None:
            self.api = WindowsAPI()
        identity = (pid, created)
        previous = self._handles.get(name)
        if previous and previous[:2] != identity:
            self.api.close(previous[2])
            del self._handles[name]
            previous = None
        if previous:
            handle = previous[2]
        else:
            handle, error = self.api.open(pid)
            if not handle:
                if error == 87:
                    return ProcessState("missing", error_code="PROCESS_EXIT_UNOBSERVED")
                return ProcessState("error", error_code="PROCESS_ACCESS_DENIED" if error == 5 else "PROCESS_OPEN_FAILED")
            actual = self.api.created(handle)
            if actual != created:
                self.api.close(handle)
                return ProcessState("error" if actual is None else "identity_mismatch",
                                    error_code="PROCESS_TIME_FAILED" if actual is None else "PROCESS_IDENTITY_MISMATCH")
            self._handles[name] = (*identity, handle)
        wait = self.api.wait(handle)
        if wait == 0x102:
            return ProcessState("alive")
        if wait != 0:
            return ProcessState("error", error_code="PROCESS_WAIT_FAILED")
        code = self.api.exit_code(handle)
        if code is None:
            return ProcessState("error", error_code="PROCESS_EXIT_CODE_FAILED")
        # Wait established termination, even if the exit code happens to be 259.
        return ProcessState("exited", exit_code=code)

    def retain(self, names):
        for name in list(self._handles):
            if name not in names:
                self.api.close(self._handles.pop(name)[2])

    def close(self):
        self.retain(set())
