from __future__ import annotations

import ctypes
import hashlib
import os
import socket
import struct
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .detector import KNOWN_AUTOPAINT_BRIDGE_HASHES, REQUIRED_AUTOPAINT_MARKERS


if os.name != "nt":
    raise RuntimeError("The live sensor is supported only on Windows")


TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPTHREAD = 0x00000004
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
THREAD_QUERY_INFORMATION = 0x0040
THREAD_QUERY_LIMITED_INFORMATION = 0x0800
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260
MAX_MODULE_NAME32 = 255


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(wintypes.BYTE)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * (MAX_MODULE_NAME32 + 1)),
        ("szExePath", wintypes.WCHAR * MAX_PATH),
    ]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


class MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pcwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.c_void_p),
    ]


class WINTRUST_DATA(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPCWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
wintrust = ctypes.WinDLL("wintrust", use_last_error=True)

kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32FirstW.restype = wintypes.BOOL
kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.restype = wintypes.BOOL
kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
kernel32.Thread32First.restype = wintypes.BOOL
kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
kernel32.Thread32Next.restype = wintypes.BOOL
kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenThread.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
ntdll.NtQueryInformationThread.argtypes = [
    wintypes.HANDLE,
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]
ntdll.NtQueryInformationThread.restype = wintypes.LONG
iphlpapi.GetExtendedTcpTable.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.ULONG),
    wintypes.BOOL,
    wintypes.ULONG,
    wintypes.ULONG,
    wintypes.ULONG,
]
iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD
wintrust.WinVerifyTrust.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(GUID),
    ctypes.POINTER(WINTRUST_DATA),
]
wintrust.WinVerifyTrust.restype = wintypes.LONG


def _snapshot(flags: int, pid: int = 0) -> int:
    handle = kernel32.CreateToolhelp32Snapshot(flags, pid)
    if handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _process_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def enumerate_processes() -> list[dict[str, Any]]:
    handle = _snapshot(TH32CS_SNAPPROCESS)
    items: list[dict[str, Any]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            items.append(
                {
                    "pid": int(entry.th32ProcessID),
                    "parent_pid": int(entry.th32ParentProcessID),
                    "name": entry.szExeFile,
                }
            )
            ok = kernel32.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(handle)
    return items


def enumerate_modules(pid: int) -> list[dict[str, Any]]:
    handle = _snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    items: list[dict[str, Any]] = []
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Module32FirstW(handle, ctypes.byref(entry))
        while ok:
            base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0
            items.append(
                {
                    "name": entry.szModule,
                    "path": entry.szExePath,
                    "base_address": int(base),
                    "size": int(entry.modBaseSize),
                }
            )
            ok = kernel32.Module32NextW(handle, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(handle)
    return items


def _thread_start_address(thread_id: int) -> int | None:
    handle = kernel32.OpenThread(THREAD_QUERY_INFORMATION, False, thread_id)
    if not handle:
        handle = kernel32.OpenThread(THREAD_QUERY_LIMITED_INFORMATION, False, thread_id)
    if not handle:
        return None
    try:
        address = ctypes.c_void_p()
        returned = wintypes.ULONG()
        status = ntdll.NtQueryInformationThread(
            handle,
            9,
            ctypes.byref(address),
            ctypes.sizeof(address),
            ctypes.byref(returned),
        )
        return int(address.value) if status == 0 and address.value else None
    finally:
        kernel32.CloseHandle(handle)


def enumerate_threads(pid: int, modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    handle = _snapshot(TH32CS_SNAPTHREAD)
    items: list[dict[str, Any]] = []
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Thread32First(handle, ctypes.byref(entry))
        while ok:
            if int(entry.th32OwnerProcessID) == pid:
                thread_id = int(entry.th32ThreadID)
                start = _thread_start_address(thread_id)
                origin = ""
                if start is not None:
                    for module in modules:
                        base = int(module["base_address"])
                        if base <= start < base + int(module["size"]):
                            origin = str(module["path"])
                            break
                items.append(
                    {
                        "thread_id": thread_id,
                        "start_address": start,
                        "origin_module": origin,
                    }
                )
            ok = kernel32.Thread32Next(handle, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(handle)
    return items


def enumerate_tcp(pid: int) -> list[dict[str, Any]]:
    size = wintypes.ULONG(0)
    iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0
    )
    if not size.value:
        return []
    buffer = ctypes.create_string_buffer(size.value)
    result = iphlpapi.GetExtendedTcpTable(
        buffer, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0
    )
    if result != 0:
        raise OSError(result, "GetExtendedTcpTable failed")

    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    base = ctypes.addressof(buffer) + ctypes.sizeof(wintypes.DWORD)
    row_size = ctypes.sizeof(MIB_TCPROW_OWNER_PID)
    rows: list[dict[str, Any]] = []
    for index in range(count):
        row = MIB_TCPROW_OWNER_PID.from_address(base + index * row_size)
        if int(row.dwOwningPid) != pid:
            continue
        local_ip = socket.inet_ntoa(struct.pack("<I", int(row.dwLocalAddr)))
        remote_ip = socket.inet_ntoa(struct.pack("<I", int(row.dwRemoteAddr)))
        rows.append(
            {
                "state": int(row.dwState),
                "local_ip": local_ip,
                "local_port": socket.ntohs(int(row.dwLocalPort) & 0xFFFF),
                "remote_ip": remote_ip,
                "remote_port": socket.ntohs(int(row.dwRemotePort) & 0xFFFF),
            }
        )
    return rows


def _authenticode_status(path: Path) -> str:
    action = GUID(
        0x00AAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )
    file_info = WINTRUST_FILE_INFO()
    file_info.cbStruct = ctypes.sizeof(file_info)
    file_info.pcwszFilePath = str(path)
    data = WINTRUST_DATA()
    data.cbStruct = ctypes.sizeof(data)
    data.dwUIChoice = 2
    data.fdwRevocationChecks = 0
    data.dwUnionChoice = 1
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = 0
    data.dwProvFlags = 0x00001000
    try:
        result = wintrust.WinVerifyTrust(
            ctypes.c_void_p(INVALID_HANDLE_VALUE), ctypes.byref(action), ctypes.byref(data)
        )
    except OSError:
        return "unknown"
    return "trusted" if result == 0 else "untrusted"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _find_markers(path: Path) -> list[str]:
    needles = {marker: marker.encode("ascii") for marker in REQUIRED_AUTOPAINT_MARKERS}
    found: set[str] = set()
    overlap = max(map(len, needles.values())) - 1
    tail = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            data = tail + chunk
            for marker, needle in needles.items():
                if needle in data:
                    found.add(marker)
            if len(found) == len(needles):
                break
            tail = data[-overlap:] if overlap > 0 else b""
    return sorted(found)


def _normal(path: Path) -> str:
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path.absolute()).casefold()


def _is_below(path: Path, roots: list[Path]) -> bool:
    candidate = _normal(path)
    for root in roots:
        normalized_root = _normal(root).rstrip("\\/") + os.sep
        if candidate.startswith(normalized_root):
            return True
    return False


class WindowsClientSensor:
    def __init__(self, process_name: str, *, pid: int | None = None) -> None:
        self.process_name = process_name
        self.requested_pid = pid
        self.started_ns = time.monotonic_ns()
        self._file_cache: dict[tuple[str, int, int], tuple[str, list[str], str]] = {}
        self._user_roots = [
            Path(value)
            for key in ("LOCALAPPDATA", "APPDATA", "TEMP", "TMP")
            if (value := os.environ.get(key))
        ]

    def _inspect_module(self, module: dict[str, Any]) -> dict[str, Any] | None:
        path = Path(str(module["path"]))
        try:
            stat = path.stat()
        except OSError:
            return None

        cache_key = (_normal(path), stat.st_size, stat.st_mtime_ns)
        cached = self._file_cache.get(cache_key)
        if cached is None:
            if stat.st_size <= 64 * 1024 * 1024:
                try:
                    digest = _sha256(path)
                    markers = _find_markers(path)
                except OSError:
                    digest, markers = "", []
            else:
                digest, markers = "", []
            user_writable = _is_below(path, self._user_roots)
            signature = _authenticode_status(path) if user_writable else "not_checked"
            cached = (digest, markers, signature)
            self._file_cache[cache_key] = cached
        digest, markers, signature = cached

        normalized = _normal(path).replace("/", "\\")
        autopaint_path = (
            "\\mecchacamouflage\\lite\\runtime\\" in normalized
            or path.name.casefold() in {"runtime-bridge.dll"}
            or path.name.casefold().startswith("meccha-direct-bridge-v1-")
        )
        user_writable = _is_below(path, self._user_roots)
        is_candidate = (
            digest.casefold() in KNOWN_AUTOPAINT_BRIDGE_HASHES
            or REQUIRED_AUTOPAINT_MARKERS.issubset(set(markers))
            or autopaint_path
            or (user_writable and signature == "untrusted")
        )
        if not is_candidate:
            return None
        return {
            **module,
            "sha256": digest,
            "markers": markers,
            "signature": signature,
            "user_writable_path": user_writable,
            "autopaint_runtime_path": autopaint_path,
        }

    def _artifacts(self) -> list[dict[str, Any]]:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return []
        runtime = Path(local_app_data) / "MecchaCamouflage" / "lite" / "runtime"
        if not runtime.is_dir():
            return []
        artifacts: list[dict[str, Any]] = []
        for sidecar in runtime.glob("bridge-instance-*/*.dll.port"):
            try:
                port = int(sidecar.read_text(encoding="ascii").strip())
            except (OSError, UnicodeError, ValueError):
                port = 0
            artifacts.append(
                {"kind": "autopaint_port_sidecar", "path": str(sidecar), "port": port}
            )
        for binary in runtime.glob("bridge-instance-*/*.dll"):
            artifacts.append(
                {"kind": "autopaint_runtime_file", "path": str(binary), "port": 0}
            )
        return artifacts

    def collect(self) -> dict[str, Any]:
        timestamp_ms = (time.monotonic_ns() - self.started_ns) // 1_000_000
        errors: list[str] = []
        processes = enumerate_processes()
        target = None
        if self.requested_pid is not None:
            target = next(
                (item for item in processes if item["pid"] == self.requested_pid), None
            )
        else:
            expected = self.process_name.casefold()
            target = next(
                (item for item in processes if str(item["name"]).casefold() == expected), None
            )

        interesting_processes = []
        interesting_names = {
            "runtime-injector.exe",
            "meccha-chameleon-litev2.exe",
        }
        for process in processes:
            if str(process["name"]).casefold() in interesting_names:
                interesting_processes.append(
                    {**process, "path": _process_path(int(process["pid"]))}
                )

        snapshot: dict[str, Any] = {
            "schema_version": 1,
            "sensor": "windows_client_integrity",
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "timestamp_ms": int(timestamp_ms),
            "target": {
                "found": target is not None,
                "name": self.process_name,
                "pid": int(target["pid"]) if target else 0,
            },
            "processes": interesting_processes,
            "modules": [],
            "threads": [],
            "tcp_endpoints": [],
            "artifacts": self._artifacts(),
            "sensor_errors": errors,
        }
        if target is None:
            return snapshot

        pid = int(target["pid"])
        try:
            all_modules = enumerate_modules(pid)
        except OSError as exc:
            errors.append(f"module_enumeration_failed:{exc.winerror or exc.errno}")
            snapshot["sensor_healthy"] = False
            return snapshot

        inspected_modules = [
            candidate
            for module in all_modules
            if (candidate := self._inspect_module(module)) is not None
        ]
        snapshot["module_count"] = len(all_modules)
        snapshot["modules"] = inspected_modules

        try:
            all_threads = enumerate_threads(pid, all_modules)
            suspicious_paths = {
                str(module["path"]).casefold() for module in inspected_modules
            }
            snapshot["thread_count"] = len(all_threads)
            snapshot["threads"] = [
                thread
                for thread in all_threads
                if str(thread.get("origin_module", "")).casefold() in suspicious_paths
            ]
        except OSError as exc:
            errors.append(f"thread_enumeration_failed:{exc.winerror or exc.errno}")

        try:
            snapshot["tcp_endpoints"] = [
                endpoint
                for endpoint in enumerate_tcp(pid)
                if endpoint["local_ip"] in {"127.0.0.1", "::1"}
            ]
        except OSError as exc:
            errors.append(f"tcp_enumeration_failed:{exc.errno}")

        snapshot["sensor_healthy"] = True
        return snapshot

