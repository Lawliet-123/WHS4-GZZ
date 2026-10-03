"""Read-only snapshots of modules loaded in a Windows process.

The module deliberately separates acquisition from interpretation:

* :func:`enumerate_process_modules` is the Windows Toolhelp32 adapter.
* :class:`ToolhelpModuleSensor` timestamps and packages acquired facts.
* :func:`diff_module_snapshots` is a deterministic, side-effect-free detector.
* :class:`ModuleChangeDetector` retains the last good baseline per PID.

A newly observed module is only a change in process state.  It is not, by
itself, proof of DLL injection or cheating.  Signature, path, hash, provenance,
and correlation with other sensors belong in later detector/policy stages.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import math
import ntpath
import os
import time
from typing import Callable, Iterable, Sequence


TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
MAX_MODULE_NAME32 = 255
MAX_PATH = 260
ERROR_NO_MORE_FILES = 18
ERROR_BAD_LENGTH = 24
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class ModuleEnumerationError(RuntimeError):
    """The target module list could not be acquired reliably."""

    def __init__(self, pid: int, operation: str, winerror: int | None) -> None:
        self.pid = int(pid)
        self.operation = str(operation)
        self.winerror = None if winerror is None else int(winerror)
        suffix = (
            f" (WinError {self.winerror})" if self.winerror is not None else ""
        )
        super().__init__(
            f"could not enumerate modules for PID {self.pid}: "
            f"{self.operation}{suffix}"
        )


def canonical_module_path(path: str) -> str:
    """Return a stable, case-insensitive Windows path used only for matching."""

    if not isinstance(path, str):
        raise TypeError("path must be a string")
    value = path.strip().replace("/", "\\")
    if not value:
        raise ValueError("path must be non-empty")

    # Tooling can report the same file with Win32 or NT path prefixes.  Remove
    # those prefixes for comparison while retaining the original path in logs.
    lowered = value.casefold()
    if lowered.startswith("\\\\?\\unc\\"):
        value = "\\\\" + value[8:]
    elif lowered.startswith("\\\\?\\"):
        value = value[4:]
    elif lowered.startswith("\\??\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


@dataclass(frozen=True, slots=True)
class ModuleInfo:
    """One loaded image as reported by ``MODULEENTRY32W``."""

    name: str
    path: str
    base_address: int
    image_size: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("path must be a non-empty string")
        if (
            isinstance(self.base_address, bool)
            or not isinstance(self.base_address, int)
            or self.base_address < 0
        ):
            raise ValueError("base_address must be a non-negative integer")
        if (
            isinstance(self.image_size, bool)
            or not isinstance(self.image_size, int)
            or self.image_size < 0
        ):
            raise ValueError("image_size must be a non-negative integer")

    @property
    def identity(self) -> str:
        """Case-insensitive full-path identity used by snapshot comparison."""

        return canonical_module_path(self.path)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": self.path,
            "base_address": self.base_address,
            "base_address_hex": f"0x{self.base_address:X}",
            "image_size": self.image_size,
        }


def _module_sort_key(module: ModuleInfo) -> tuple[str, int, int]:
    return (module.identity, module.base_address, module.image_size)


@dataclass(frozen=True, slots=True)
class ModuleSnapshot:
    """An immutable module list acquired at one point in time."""

    pid: int
    captured_at: float
    modules: tuple[ModuleInfo, ...]

    def __post_init__(self) -> None:
        if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0:
            raise ValueError("pid must be a positive integer")
        if (
            isinstance(self.captured_at, bool)
            or not isinstance(self.captured_at, (int, float))
            or not math.isfinite(float(self.captured_at))
            or self.captured_at < 0
        ):
            raise ValueError("captured_at must be a non-negative number")
        raw_modules = tuple(self.modules)
        if any(not isinstance(module, ModuleInfo) for module in raw_modules):
            raise TypeError("modules must contain ModuleInfo values")
        normalized = tuple(sorted(raw_modules, key=_module_sort_key))
        identities = [module.identity for module in normalized]
        if len(identities) != len(set(identities)):
            raise ValueError("a snapshot cannot contain duplicate module paths")
        object.__setattr__(self, "captured_at", float(self.captured_at))
        object.__setattr__(self, "modules", normalized)


@dataclass(frozen=True, slots=True)
class ModuleChange:
    """A module present at the same path with changed address or image size."""

    before: ModuleInfo
    after: ModuleInfo

    def __post_init__(self) -> None:
        if self.before.identity != self.after.identity:
            raise ValueError("changed modules must refer to the same path")


@dataclass(frozen=True, slots=True)
class ModuleDiff:
    """Factual differences between two successful module snapshots."""

    pid: int
    previous_captured_at: float | None
    current_captured_at: float
    added: tuple[ModuleInfo, ...] = ()
    removed: tuple[ModuleInfo, ...] = ()
    changed: tuple[ModuleChange, ...] = ()
    baseline_created: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def _index_by_path(modules: Iterable[ModuleInfo]) -> dict[str, ModuleInfo]:
    indexed: dict[str, ModuleInfo] = {}
    for module in modules:
        if not isinstance(module, ModuleInfo):
            raise TypeError("module collections must contain ModuleInfo values")
        if module.identity in indexed:
            raise ValueError("module collections cannot contain duplicate paths")
        indexed[module.identity] = module
    return indexed


def diff_module_snapshots(
    previous: ModuleSnapshot,
    current: ModuleSnapshot,
) -> ModuleDiff:
    """Compare snapshots without performing I/O or assigning suspicion.

    Paths are compared case-insensitively using Windows path rules.  A path not
    present previously is ``added``.  The same path at a different base address
    or with a different image size is ``changed`` rather than being mislabeled
    as a newly injected DLL.
    """

    if not isinstance(previous, ModuleSnapshot) or not isinstance(
        current, ModuleSnapshot
    ):
        raise TypeError("previous and current must be ModuleSnapshot values")
    if previous.pid != current.pid:
        raise ValueError("cannot compare snapshots from different PIDs")

    old = _index_by_path(previous.modules)
    new = _index_by_path(current.modules)
    added = tuple(
        sorted(
            (new[key] for key in new.keys() - old.keys()),
            key=_module_sort_key,
        )
    )
    removed = tuple(
        sorted((old[key] for key in old.keys() - new.keys()), key=_module_sort_key)
    )
    changed = tuple(
        ModuleChange(old[key], new[key])
        for key in sorted(old.keys() & new.keys())
        if (
            old[key].base_address != new[key].base_address
            or old[key].image_size != new[key].image_size
        )
    )
    return ModuleDiff(
        pid=current.pid,
        previous_captured_at=previous.captured_at,
        current_captured_at=current.captured_at,
        added=added,
        removed=removed,
        changed=changed,
    )


_IS_WINDOWS = os.name == "nt"

if _IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _MODULEENTRY32W(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("th32ModuleID", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("GlblcntUsage", wintypes.DWORD),
            ("ProccntUsage", wintypes.DWORD),
            ("modBaseAddr", ctypes.POINTER(ctypes.c_ubyte)),
            ("modBaseSize", wintypes.DWORD),
            ("hModule", wintypes.HMODULE),
            ("szModule", wintypes.WCHAR * (MAX_MODULE_NAME32 + 1)),
            ("szExePath", wintypes.WCHAR * MAX_PATH),
        )

    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Module32FirstW.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_MODULEENTRY32W),
    )
    _kernel32.Module32FirstW.restype = wintypes.BOOL
    _kernel32.Module32NextW.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_MODULEENTRY32W),
    )
    _kernel32.Module32NextW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _valid_pid(pid: int) -> int:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    return pid


def _invalid_handle(handle: object) -> bool:
    return handle is None or int(handle) in (0, INVALID_HANDLE_VALUE)


def _create_module_snapshot_handle(pid: int, attempts: int = 4):
    flags = TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32
    last_error: int | None = None
    for _ in range(attempts):
        ctypes.set_last_error(0)
        handle = _kernel32.CreateToolhelp32Snapshot(flags, pid)
        if not _invalid_handle(handle):
            return handle
        last_error = ctypes.get_last_error()
        if last_error != ERROR_BAD_LENGTH:
            break
    raise ModuleEnumerationError(pid, "CreateToolhelp32Snapshot", last_error)


def enumerate_process_modules(pid: int) -> tuple[ModuleInfo, ...]:
    """Enumerate modules in *pid* using ``CreateToolhelp32Snapshot``.

    Errors are explicit so callers can mark the sensor unavailable instead of
    interpreting an access-denied or cross-architecture failure as an empty,
    healthy process.  No target-process memory is written.
    """

    pid = _valid_pid(pid)
    if not _IS_WINDOWS:
        raise OSError("module enumeration is available only on Windows")

    handle = _create_module_snapshot_handle(pid)
    try:
        entry = _MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(_MODULEENTRY32W)
        ctypes.set_last_error(0)
        if not _kernel32.Module32FirstW(handle, ctypes.byref(entry)):
            error = ctypes.get_last_error()
            if error == ERROR_NO_MORE_FILES:
                return ()
            raise ModuleEnumerationError(pid, "Module32FirstW", error)

        modules: list[ModuleInfo] = []
        while True:
            path = str(entry.szExePath).strip() or str(entry.szModule).strip()
            name = str(entry.szModule).strip() or ntpath.basename(path)
            base_address = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0
            modules.append(
                ModuleInfo(
                    name=name,
                    path=path,
                    base_address=int(base_address),
                    image_size=int(entry.modBaseSize),
                )
            )

            entry.dwSize = ctypes.sizeof(_MODULEENTRY32W)
            ctypes.set_last_error(0)
            if not _kernel32.Module32NextW(handle, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != ERROR_NO_MORE_FILES:
                    raise ModuleEnumerationError(pid, "Module32NextW", error)
                break

        return tuple(sorted(modules, key=_module_sort_key))
    finally:
        _kernel32.CloseHandle(handle)


ModuleProvider = Callable[[int], Sequence[ModuleInfo]]


class ToolhelpModuleSensor:
    """Acquire timestamped module snapshots through an injectable provider."""

    def __init__(
        self,
        *,
        module_provider: ModuleProvider = enumerate_process_modules,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._module_provider = module_provider
        self._clock = clock

    def capture(self, pid: int) -> ModuleSnapshot:
        pid = _valid_pid(pid)
        modules = tuple(self._module_provider(pid))
        return ModuleSnapshot(pid=pid, captured_at=self._clock(), modules=modules)


class ModuleChangeDetector:
    """Maintain the last successful snapshot for each observed PID.

    The first successful snapshot is a baseline and emits no additions.  If the
    sensor raises, callers should retain this detector and try again later: the
    previous good baseline remains unchanged because ``observe`` was not called.
    """

    def __init__(self) -> None:
        self._previous_by_pid: dict[int, ModuleSnapshot] = {}

    def observe(self, snapshot: ModuleSnapshot) -> ModuleDiff:
        if not isinstance(snapshot, ModuleSnapshot):
            raise TypeError("snapshot must be a ModuleSnapshot")
        previous = self._previous_by_pid.get(snapshot.pid)
        self._previous_by_pid[snapshot.pid] = snapshot
        if previous is None:
            return ModuleDiff(
                pid=snapshot.pid,
                previous_captured_at=None,
                current_captured_at=snapshot.captured_at,
                baseline_created=True,
            )
        return diff_module_snapshots(previous, snapshot)

    def reset(self, pid: int | None = None) -> None:
        """Forget one process baseline, or every baseline when *pid* is omitted."""

        if pid is None:
            self._previous_by_pid.clear()
            return
        self._previous_by_pid.pop(_valid_pid(pid), None)


__all__ = [
    "ERROR_BAD_LENGTH",
    "ERROR_NO_MORE_FILES",
    "MAX_MODULE_NAME32",
    "MAX_PATH",
    "TH32CS_SNAPMODULE",
    "TH32CS_SNAPMODULE32",
    "ModuleChange",
    "ModuleChangeDetector",
    "ModuleDiff",
    "ModuleEnumerationError",
    "ModuleInfo",
    "ModuleSnapshot",
    "ToolhelpModuleSensor",
    "canonical_module_path",
    "diff_module_snapshots",
    "enumerate_process_modules",
]
