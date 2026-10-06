"""Documented Windows queries only; no debug attachment or process mutation."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os

PROCESS_QUERY_INFORMATION = 0x0400
SYNCHRONIZE = 0x00100000
QUERY_ACCESS = PROCESS_QUERY_INFORMATION | SYNCHRONIZE
WAIT_TIMEOUT, WAIT_OBJECT_0 = 0x102, 0


@dataclass(frozen=True)
class Observation:
    state: str
    debugger_present: bool | None = None
    observed_create_time: int | None = None
    error_code: str | None = None
    winerror: int | None = None


def valid_identity(pid, created):
    return (type(pid) is int and 0 < pid <= 0xFFFFFFFF
            and type(created) is int and 0 < created <= 0xFFFFFFFFFFFFFFFF)


class WindowsAPI:
    def __init__(self):
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "GetProcessTimes": ([wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
            "CheckRemoteDebuggerPresent": ([wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
        }
        for name, (args, result) in declarations.items():
            function = getattr(self.k32, name)
            function.argtypes, function.restype = args, result

    def open(self, pid):
        ctypes.set_last_error(0)
        handle = self.k32.OpenProcess(QUERY_ACCESS, False, pid)
        return handle, 0 if handle else ctypes.get_last_error()

    def close(self, handle):
        self.k32.CloseHandle(handle)

    def alive(self, handle):
        code = self.k32.WaitForSingleObject(handle, 0)
        if code == WAIT_TIMEOUT:
            return True, 0
        if code == WAIT_OBJECT_0:
            return False, 0
        return None, ctypes.get_last_error()

    def created(self, handle):
        times = [wintypes.FILETIME() for _ in range(4)]
        if not self.k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None, ctypes.get_last_error()
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime, 0

    def debugger(self, handle):
        present = wintypes.BOOL(False)
        if not self.k32.CheckRemoteDebuggerPresent(handle, ctypes.byref(present)):
            return None, ctypes.get_last_error()
        # API success is not debugger presence: read the out parameter separately.
        return bool(present.value), 0


class Probe:
    def __init__(self, api=None):
        self.api = api

    def inspect(self, pid, expected_create_time=None):
        # Only the running monitor's own identity may be discovered without a pin.
        if expected_create_time is None:
            if pid != os.getpid():
                return Observation("ERROR", error_code="EXPECTED_IDENTITY_REQUIRED")
        elif not valid_identity(pid, expected_create_time):
            return Observation("ERROR", error_code="INVALID_TARGET_IDENTITY")
        if self.api is None:
            if os.name != "nt":
                return Observation("ERROR", error_code="PLATFORM_UNSUPPORTED")
            try:
                self.api = WindowsAPI()
            except (OSError, AttributeError):
                return Observation("ERROR", error_code="WINDOWS_API_UNAVAILABLE")
        handle, error = self.api.open(pid)
        if not handle:
            if error == 87:
                return Observation("EXITED", error_code="TARGET_NOT_RUNNING", winerror=error)
            return Observation("ERROR", error_code="ACCESS_DENIED" if error == 5 else "OPEN_PROCESS_FAILED", winerror=error)
        created = None
        try:
            created, error = self.api.created(handle)
            if created is None:
                return Observation("ERROR", error_code="IDENTITY_QUERY_FAILED", winerror=error)
            if expected_create_time is not None and created != expected_create_time:
                return Observation("ERROR", observed_create_time=created, error_code="PROCESS_IDENTITY_MISMATCH")
            alive, error = self.api.alive(handle)
            if alive is None:
                return Observation("ERROR", observed_create_time=created, error_code="LIVENESS_QUERY_FAILED", winerror=error)
            if not alive:
                return Observation("EXITED", observed_create_time=created, error_code="TARGET_EXITED")
            present, error = self.api.debugger(handle)
            if present is None:
                return Observation("ERROR", observed_create_time=created, error_code="DEBUGGER_QUERY_FAILED", winerror=error)
            # Keep a positive observation even if the target exits immediately afterwards.
            if present:
                return Observation("DEBUGGER_PRESENT", True, created)
            alive, error = self.api.alive(handle)
            if alive is None:
                return Observation("ERROR", observed_create_time=created, error_code="LIVENESS_QUERY_FAILED", winerror=error)
            if not alive:
                return Observation("EXITED", observed_create_time=created, error_code="TARGET_EXITED_DURING_CHECK")
            return Observation("CLEAR", False, created)
        finally:
            self.api.close(handle)
