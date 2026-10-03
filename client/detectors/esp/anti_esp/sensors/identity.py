"""Opt-in, pseudonymous endpoint identity for anti-cheat telemetry.

Raw endpoint identifiers are used only as transient local input to a keyed
hash.  They are never returned by this module, written to the installation
secret file, or included in :class:`IdentityObservation`.

Without an externally supplied stable pepper, the result is intentionally an
*installation pseudonym*: reinstalling the guard or deleting its secret will
change it.  With a pepper it can be a more stable endpoint pseudonym, but it is
still not a guaranteed immutable HWID because hardware and OS identifiers can
change or be unavailable.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import hmac
import math
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Callable, Literal, Mapping, Protocol
import unicodedata


IdentityStatus = Literal["disabled", "generated", "unavailable", "error"]
IdentityScope = Literal["none", "installation", "endpoint"]


_PLACEHOLDER_VALUES = frozenset(
    {
        "none",
        "null",
        "unknown",
        "not available",
        "not applicable",
        "to be filled by o.e.m.",
        "default string",
        "system serial number",
        "0",
        "00000000",
        "00000000-0000-0000-0000-000000000000",
    }
)

_PUBLIC_SOURCE_KINDS = {
    "machine_guid": "os_installation",
    "system_volume_serial": "system_volume",
    "board": "mainboard",
    "motherboard": "mainboard",
    "baseboard": "mainboard",
    "disk": "storage",
    "drive": "storage",
    "bios": "firmware",
}


class IdentitySourceUnavailable(RuntimeError):
    """Raised when no supported endpoint identifier can be acquired."""


class IdentifierProvider(Protocol):
    def collect(self) -> Mapping[str, str]:
        """Return raw identifiers for immediate, in-memory pseudonymization."""


@dataclass(frozen=True, slots=True)
class IdentityObservation:
    """Privacy-bounded identity result safe to place in telemetry."""

    status: IdentityStatus
    scope: IdentityScope
    generated_at: float
    pseudonym: str | None = None
    algorithm: str = "HMAC-SHA-256/v1"
    source_kinds: tuple[str, ...] = ()
    message: str = ""
    stability_guaranteed: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"disabled", "generated", "unavailable", "error"}:
            raise ValueError("unsupported identity status")
        if self.scope not in {"none", "installation", "endpoint"}:
            raise ValueError("unsupported identity scope")
        if (
            isinstance(self.generated_at, bool)
            or not isinstance(self.generated_at, (int, float))
            or not math.isfinite(float(self.generated_at))
            or self.generated_at < 0
        ):
            raise ValueError("generated_at must be a non-negative finite number")
        if self.status == "generated" and not self.pseudonym:
            raise ValueError("generated identities require a pseudonym")
        if self.status != "generated" and self.pseudonym is not None:
            raise ValueError("non-generated results cannot contain a pseudonym")
        if any(not isinstance(kind, str) or not kind for kind in self.source_kinds):
            raise ValueError("source_kinds must contain non-empty strings")
        if self.stability_guaranteed:
            raise ValueError("endpoint identity stability must not be guaranteed")

    def to_dict(self) -> dict[str, object]:
        """Return telemetry fields that never include raw identifiers."""

        return {
            "status": self.status,
            "scope": self.scope,
            "generated_at": float(self.generated_at),
            "pseudonym": self.pseudonym,
            "algorithm": self.algorithm,
            "source_kinds": list(self.source_kinds),
            "message": self.message,
            "stability_guaranteed": False,
        }


def normalize_identifier(value: str) -> str | None:
    """Normalize one identifier, rejecting common firmware placeholders."""

    if not isinstance(value, str):
        raise TypeError("identifier values must be strings")
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = " ".join(normalized.split())
    if not normalized or normalized in _PLACEHOLDER_VALUES:
        return None
    compact = normalized.replace("-", "").replace(" ", "")
    if compact and set(compact) <= {"0"}:
        return None
    return normalized


def _canonical_identifier_bytes(values: Mapping[str, str]) -> tuple[bytes, tuple[str, ...]]:
    if not isinstance(values, Mapping):
        raise TypeError("identifier provider must return a mapping")
    parts: list[bytes] = []
    kinds: set[str] = set()
    for raw_kind in sorted(values, key=lambda key: str(key).casefold()):
        if not isinstance(raw_kind, str) or not raw_kind.strip():
            raise TypeError("identifier names must be non-empty strings")
        kind = unicodedata.normalize("NFKC", raw_kind).strip().casefold()
        normalized = normalize_identifier(values[raw_kind])
        if normalized is None:
            continue
        kind_bytes = kind.encode("utf-8")
        value_bytes = normalized.encode("utf-8")
        # Length framing prevents ambiguous concatenation of source/value pairs.
        parts.append(len(kind_bytes).to_bytes(4, "big") + kind_bytes)
        parts.append(len(value_bytes).to_bytes(4, "big") + value_bytes)
        # Only a small vocabulary is safe to emit.  An injected provider may
        # accidentally place a serial value in the mapping key, so unknown
        # names are represented as "custom" instead of being copied to logs.
        kinds.add(_PUBLIC_SOURCE_KINDS.get(kind, "custom"))
    if not parts:
        raise IdentitySourceUnavailable("no usable endpoint identifiers were found")
    return b"".join(parts), tuple(sorted(kinds))


def default_installation_secret_path() -> Path:
    """Return the per-user LocalGuard secret path without creating it."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base / "MecchaLocalGuard" / "identity.secret"


class FileInstallationSecret:
    """Create/read a random local secret; no hardware data is stored here."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else default_installation_secret_path()
        self._lock = threading.Lock()

    def get(self) -> bytes:
        with self._lock:
            try:
                existing = self.path.read_bytes()
            except FileNotFoundError:
                existing = b""
            if existing:
                if len(existing) < 32:
                    raise ValueError("installation secret is shorter than 32 bytes")
                return existing

            self.path.parent.mkdir(parents=True, exist_ok=True)
            generated = secrets.token_bytes(32)
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_BINARY", 0),
                    0o600,
                )
            except FileExistsError:
                existing = self.path.read_bytes()
                if len(existing) < 32:
                    raise ValueError("installation secret is shorter than 32 bytes")
                return existing
            try:
                os.write(descriptor, generated)
            finally:
                os.close(descriptor)
            return generated


def _collect_windows_endpoint_identifiers() -> Mapping[str, str]:
    """Collect two Windows endpoint identifiers for immediate hashing.

    ``MachineGuid`` is an OS installation identifier, not a motherboard serial.
    The system volume serial is filesystem metadata, not a physical disk serial.
    These limitations are why the result is described as a pseudonym rather
    than a guaranteed HWID.
    """

    if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
        raise IdentitySourceUnavailable(
            "default endpoint identifiers are available only on Windows"
        )

    values: dict[str, str] = {}
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        ) as key:
            machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
        if isinstance(machine_guid, str):
            values["machine_guid"] = machine_guid
    except OSError:
        pass

    system_drive = os.environ.get("SystemDrive", "C:")
    root = system_drive.rstrip("\\/") + "\\"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetVolumeInformationW
    function.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    function.restype = wintypes.BOOL
    serial = wintypes.DWORD()
    if function(root, None, 0, ctypes.byref(serial), None, None, None, 0):
        values["system_volume_serial"] = f"{serial.value:08X}"

    if not values:
        raise IdentitySourceUnavailable("Windows endpoint identifiers are unavailable")
    return values


class WindowsEndpointIdentifierProvider:
    """Acquire Windows installation/volume facts for immediate hashing."""

    def collect(self) -> Mapping[str, str]:
        return _collect_windows_endpoint_identifiers()


class PseudonymousIdentity:
    """Generate an opt-in, namespace-bound endpoint pseudonym.

    When ``pepper`` is absent, a random per-installation secret is used and the
    scope is ``installation``.  Supplying the same externally managed pepper
    on multiple installations makes the value more stable (``endpoint``), but
    stability is still explicitly not guaranteed.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        app_namespace: str = "meccha.localguard.esp.v1",
        identifier_provider: IdentifierProvider | Callable[[], Mapping[str, str]] | None = None,
        installation_secret_provider: Callable[[], bytes] | None = None,
        pepper: bytes | str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(app_namespace, str) or not app_namespace.strip():
            raise ValueError("app_namespace must be a non-empty string")
        if isinstance(pepper, str):
            pepper = pepper.encode("utf-8")
        if pepper is not None and (not isinstance(pepper, bytes) or not pepper):
            raise ValueError("pepper must be non-empty bytes, text, or None")
        self.enabled = bool(enabled)
        self.app_namespace = app_namespace.strip()
        self._identifier_provider = identifier_provider or WindowsEndpointIdentifierProvider()
        self._secret_provider = installation_secret_provider or FileInstallationSecret().get
        self._pepper = pepper
        self._clock = clock

    def _collect(self) -> Mapping[str, str]:
        provider = self._identifier_provider
        collect = getattr(provider, "collect", None)
        if collect is not None:
            return collect()
        if callable(provider):
            return provider()
        raise TypeError("identifier_provider must be callable or define collect()")

    def generate(self) -> IdentityObservation:
        now = float(self._clock())
        if not self.enabled:
            return IdentityObservation(
                status="disabled",
                scope="none",
                generated_at=now,
                message="endpoint pseudonym collection requires explicit opt-in",
            )

        try:
            canonical, kinds = _canonical_identifier_bytes(self._collect())
            namespace = self.app_namespace.encode("utf-8")
            framed_message = (
                b"meccha-endpoint-pseudonym\x00"
                + len(namespace).to_bytes(4, "big")
                + namespace
                + canonical
            )
            if self._pepper is None:
                key = self._secret_provider()
                if not isinstance(key, bytes) or len(key) < 32:
                    raise ValueError("installation secret must contain at least 32 bytes")
                scope: IdentityScope = "installation"
                message = (
                    "installation-scoped pseudonym; it can change after reinstall "
                    "or identifier changes and is not a guaranteed HWID"
                )
            else:
                key = self._pepper
                scope = "endpoint"
                message = (
                    "peppered endpoint pseudonym; identifier changes can alter it, "
                    "so it is not a guaranteed immutable HWID"
                )
            digest = hmac.new(key, framed_message, hashlib.sha256).hexdigest()
            return IdentityObservation(
                status="generated",
                scope=scope,
                generated_at=now,
                pseudonym=f"ep1_{digest}",
                source_kinds=kinds,
                message=message,
            )
        except IdentitySourceUnavailable:
            return IdentityObservation(
                status="unavailable",
                scope="none",
                generated_at=now,
                message="no usable endpoint identifier source is available",
            )
        except Exception:
            return IdentityObservation(
                status="error",
                scope="none",
                generated_at=now,
                message="could not generate endpoint pseudonym",
            )


__all__ = [
    "FileInstallationSecret",
    "IdentifierProvider",
    "IdentityObservation",
    "IdentityScope",
    "IdentitySourceUnavailable",
    "IdentityStatus",
    "PseudonymousIdentity",
    "WindowsEndpointIdentifierProvider",
    "default_installation_secret_path",
    "normalize_identifier",
]
