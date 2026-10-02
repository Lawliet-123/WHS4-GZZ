"""Authenticode signature verification as a factual LocalGuard sensor.

This module intentionally answers only one question: what did Windows report
when it evaluated a file's Authenticode signature?  A trusted signature does
not make a module safe, and an unsigned or invalid signature does not prove
that a module is malicious.  Correlation and policy belong to a detector.

``SignatureVerifier`` accepts an injected backend and fingerprint provider so
its behaviour can be tested without Windows and so callers can choose their
own cache invalidation strategy.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace
import math
import os
from pathlib import Path
import threading
import time
from typing import Callable, Literal, Protocol, Sequence

from ..core.wintrust_codes import (
    ERROR_SUCCESS,
    TRUST_E_NOSIGNATURE,
    classify_winverifytrust_code,
)

WTD_UI_NONE = 2
WTD_REVOKE_NONE = 0
WTD_CHOICE_FILE = 1
WTD_STATEACTION_VERIFY = 1
WTD_STATEACTION_CLOSE = 2
WTD_CACHE_ONLY_URL_RETRIEVAL = 0x00001000


SignatureStatus = Literal[
    "trusted",
    "unsigned",
    "rejected",
    "error",
    "unavailable",
]
FileFingerprint = tuple[int, int]


class SignatureBackendUnavailable(RuntimeError):
    """Raised when the operating-system trust provider cannot be used."""


class SignatureBackend(Protocol):
    """Low-level backend returning the raw WinVerifyTrust result code."""

    name: str

    def verify(self, path: str) -> int:
        """Return the native trust status for *path*."""


@dataclass(frozen=True, slots=True)
class SignatureCheck:
    """A structured observation from one Authenticode verification.

    ``status`` is deliberately limited to the trust API outcome.  It is not a
    malware verdict and must not be used as a stand-alone cheating verdict.
    """

    path: str
    status: SignatureStatus
    checked_at: float
    backend: str
    native_code: int | None = None
    message: str = ""
    cache_hit: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("path must be a non-empty string")
        if self.status not in {
            "trusted",
            "unsigned",
            "rejected",
            "error",
            "unavailable",
        }:
            raise ValueError("unsupported signature status")
        if (
            isinstance(self.checked_at, bool)
            or not isinstance(self.checked_at, (int, float))
            or not math.isfinite(float(self.checked_at))
            or self.checked_at < 0
        ):
            raise ValueError("checked_at must be a non-negative finite number")
        if not isinstance(self.backend, str) or not self.backend.strip():
            raise ValueError("backend must be a non-empty string")
        if self.native_code is not None and (
            isinstance(self.native_code, bool) or not isinstance(self.native_code, int)
        ):
            raise TypeError("native_code must be an integer or None")
        if not isinstance(self.message, str):
            raise TypeError("message must be a string")

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe facts; no detector classification is added."""

        return {
            "path": self.path,
            "status": self.status,
            "checked_at": float(self.checked_at),
            "backend": self.backend,
            "native_code": self.native_code,
            "native_code_hex": (
                None if self.native_code is None else f"0x{self.native_code:08X}"
            ),
            "message": self.message,
            "cache_hit": self.cache_hit,
        }


class _GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    )


class _WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = (
        ("cbStruct", wintypes.DWORD),
        ("pcwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.POINTER(_GUID)),
    )


class _WINTRUST_DATA(ctypes.Structure):
    _fields_ = (
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", wintypes.LPVOID),
        ("pSIPClientData", wintypes.LPVOID),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("pFile", ctypes.POINTER(_WINTRUST_FILE_INFO)),
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPCWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
        ("pSignatureSettings", wintypes.LPVOID),
    )


WINTRUST_ACTION_GENERIC_VERIFY_V2 = _GUID(
    0x00AAC56B,
    0xCD44,
    0x11D0,
    (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
)


class WinVerifyTrustBackend:
    """Thin ``WinVerifyTrust`` adapter.

    The cache-only revocation flag avoids turning a local scan into an
    unpredictable network operation.  It also means this sensor does not claim
    to have performed a fresh online revocation check.
    """

    name = "WinVerifyTrust"

    def __init__(self, wintrust: object | None = None) -> None:
        if wintrust is None:
            if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
                raise SignatureBackendUnavailable(
                    "WinVerifyTrust is available only on Windows"
                )
            wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
        function = getattr(wintrust, "WinVerifyTrust", None)
        if function is None:
            raise SignatureBackendUnavailable("WinVerifyTrust entry point is missing")
        # ctypes function objects allow signature declarations; simple injected
        # fakes may not, so these assignments are best-effort.
        try:
            function.argtypes = (
                wintypes.HWND,
                ctypes.POINTER(_GUID),
                ctypes.POINTER(_WINTRUST_DATA),
            )
            function.restype = wintypes.LONG
        except (AttributeError, TypeError):
            pass
        self._function = function

    def verify(self, path: str) -> int:
        file_info = _WINTRUST_FILE_INFO()
        file_info.cbStruct = ctypes.sizeof(_WINTRUST_FILE_INFO)
        file_info.pcwszFilePath = path

        trust_data = _WINTRUST_DATA()
        trust_data.cbStruct = ctypes.sizeof(_WINTRUST_DATA)
        trust_data.dwUIChoice = WTD_UI_NONE
        trust_data.fdwRevocationChecks = WTD_REVOKE_NONE
        trust_data.dwUnionChoice = WTD_CHOICE_FILE
        trust_data.pFile = ctypes.pointer(file_info)
        trust_data.dwStateAction = WTD_STATEACTION_VERIFY
        trust_data.dwProvFlags = WTD_CACHE_ONLY_URL_RETRIEVAL

        # INVALID_HANDLE_VALUE requests no interactive parent window.
        no_window = wintypes.HWND(-1)
        code = self._function(
            no_window,
            ctypes.byref(WINTRUST_ACTION_GENERIC_VERIFY_V2),
            ctypes.byref(trust_data),
        )
        normalized = int(code) & 0xFFFFFFFF

        # Release provider state even when verification failed.  Preserve the
        # original result because the CLOSE call has its own return value.
        if trust_data.hWVTStateData:
            trust_data.dwStateAction = WTD_STATEACTION_CLOSE
            self._function(
                no_window,
                ctypes.byref(WINTRUST_ACTION_GENERIC_VERIFY_V2),
                ctypes.byref(trust_data),
            )
        return normalized


def file_fingerprint(path: str) -> FileFingerprint:
    """Return the size and nanosecond mtime used for cache invalidation."""

    stat = os.stat(path)
    return int(stat.st_size), int(stat.st_mtime_ns)


def _backend_name(backend: object | None) -> str:
    if backend is None:
        return "unavailable"
    name = getattr(backend, "name", backend.__class__.__name__)
    return str(name) or backend.__class__.__name__


def _status_for_native_code(code: int) -> tuple[SignatureStatus, str]:
    outcome = classify_winverifytrust_code(code)
    if outcome == "trusted":
        return "trusted", "Windows accepted the Authenticode trust verification"
    if outcome == "unsigned":
        return "unsigned", "Windows reported that no signature was present"
    if outcome == "rejected":
        return "rejected", "Windows rejected the Authenticode trust verification"
    return "error", "WinVerifyTrust returned an indeterminate provider status"


class SignatureVerifier:
    """Verify files with a modification-aware, thread-safe result cache."""

    def __init__(
        self,
        *,
        backend: SignatureBackend | Callable[[str], int] | None = None,
        fingerprint_provider: Callable[[str], FileFingerprint] = file_fingerprint,
        clock: Callable[[], float] = time.time,
        cache_enabled: bool = True,
    ) -> None:
        if backend is None:
            try:
                backend = WinVerifyTrustBackend()
            except (OSError, SignatureBackendUnavailable):
                backend = None
        self._backend = backend
        self._fingerprint_provider = fingerprint_provider
        self._clock = clock
        self._cache_enabled = bool(cache_enabled)
        self._cache: dict[
            str, tuple[FileFingerprint, SignatureCheck]
        ] = {}
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        return self._backend is not None

    def clear_cache(self, path: str | os.PathLike[str] | None = None) -> None:
        with self._lock:
            if path is None:
                self._cache.clear()
                return
            self._cache.pop(self._cache_key(os.fspath(path)), None)

    @staticmethod
    def _cache_key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def _invoke_backend(self, path: str) -> int:
        backend = self._backend
        if backend is None:
            raise SignatureBackendUnavailable(
                "Authenticode verification is unavailable on this platform"
            )
        verify = getattr(backend, "verify", None)
        if verify is not None:
            return int(verify(path)) & 0xFFFFFFFF
        if callable(backend):
            return int(backend(path)) & 0xFFFFFFFF
        raise TypeError("signature backend must be callable or define verify(path)")

    def verify(self, path: str | os.PathLike[str]) -> SignatureCheck:
        raw_path = os.fspath(path)
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("path must be a non-empty filesystem path")
        display_path = str(Path(raw_path).absolute())
        checked_at = float(self._clock())
        backend_name = _backend_name(self._backend)

        if self._backend is None:
            return SignatureCheck(
                path=display_path,
                status="unavailable",
                checked_at=checked_at,
                backend=backend_name,
                message="Authenticode verification is unavailable on this platform",
            )

        try:
            fingerprint = self._fingerprint_provider(display_path)
            if (
                not isinstance(fingerprint, tuple)
                or len(fingerprint) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in fingerprint
                )
            ):
                raise TypeError("fingerprint provider must return (size, mtime_ns)")
        except Exception as exc:
            return SignatureCheck(
                path=display_path,
                status="error",
                checked_at=checked_at,
                backend=backend_name,
                message=f"could not fingerprint file: {exc}",
            )

        cache_key = self._cache_key(display_path)
        if self._cache_enabled:
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached is not None and cached[0] == fingerprint:
                    return replace(cached[1], cache_hit=True)

        try:
            native_code = self._invoke_backend(display_path)
            status, message = _status_for_native_code(native_code)
            result = SignatureCheck(
                path=display_path,
                status=status,
                checked_at=checked_at,
                backend=backend_name,
                native_code=native_code,
                message=message,
            )
        except SignatureBackendUnavailable as exc:
            result = SignatureCheck(
                path=display_path,
                status="unavailable",
                checked_at=checked_at,
                backend=backend_name,
                message=str(exc),
            )
        except Exception as exc:
            result = SignatureCheck(
                path=display_path,
                status="error",
                checked_at=checked_at,
                backend=backend_name,
                message=f"signature backend failed: {exc}",
            )

        if self._cache_enabled and result.status != "unavailable":
            with self._lock:
                self._cache[cache_key] = (fingerprint, result)
        return result

    def verify_many(
        self, paths: Sequence[str | os.PathLike[str]]
    ) -> tuple[SignatureCheck, ...]:
        return tuple(self.verify(path) for path in paths)


__all__ = [
    "ERROR_SUCCESS",
    "TRUST_E_NOSIGNATURE",
    "FileFingerprint",
    "SignatureBackend",
    "SignatureBackendUnavailable",
    "SignatureCheck",
    "SignatureStatus",
    "SignatureVerifier",
    "WinVerifyTrustBackend",
    "file_fingerprint",
]
