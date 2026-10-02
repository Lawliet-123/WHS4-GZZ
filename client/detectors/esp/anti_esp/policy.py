from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import AllowlistSettings


PROCESS_CREATE_THREAD = 0x0002
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_DUP_HANDLE = 0x0040

_TAMPER_MASK = PROCESS_CREATE_THREAD | PROCESS_VM_OPERATION | PROCESS_VM_WRITE


@dataclass(frozen=True)
class AccessAssessment:
    category: str
    strength: float
    reliability: float
    reason: str
    access_labels: tuple[str, ...]
    trusted: bool = False
    source_sha256: str | None = None


class FileFingerprintCache:
    """Caches hashes using path, size and mtime as the cache identity."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, int, int], str] = {}

    def sha256(self, path: str) -> str | None:
        candidate = Path(path)
        try:
            stat = candidate.stat()
        except (OSError, ValueError):
            return None
        key = (str(candidate).casefold(), stat.st_size, stat.st_mtime_ns)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        digest = hashlib.sha256()
        try:
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
        value = digest.hexdigest()
        with self._lock:
            self._cache[key] = value
        return value


def access_labels(access_mask: int) -> tuple[str, ...]:
    known = (
        (PROCESS_CREATE_THREAD, "CREATE_THREAD"),
        (PROCESS_VM_OPERATION, "VM_OPERATION"),
        (PROCESS_VM_READ, "VM_READ"),
        (PROCESS_VM_WRITE, "VM_WRITE"),
        (PROCESS_DUP_HANDLE, "DUP_HANDLE"),
    )
    return tuple(label for bit, label in known if access_mask & bit)


def _normalise_path(path: str) -> str:
    try:
        return str(Path(path)).casefold()
    except (TypeError, ValueError):
        return str(path).casefold()


def source_is_allowlisted(
    source_path: str,
    *,
    allowlist: AllowlistSettings,
    fingerprints: FileFingerprintCache,
) -> tuple[bool, str | None]:
    """Return whether a source executable is explicitly trusted.

    Exact normalized paths are checked first.  SHA-256 is only calculated when
    hash entries are configured, keeping the normal polling path inexpensive.
    The returned digest can be attached to evidence for later review.
    """

    normalised_source = _normalise_path(source_path)
    if normalised_source in allowlist.paths:
        return True, None

    source_hash: str | None = None
    if allowlist.sha256:
        source_hash = fingerprints.sha256(source_path)
        if source_hash and source_hash.lower() in allowlist.sha256:
            return True, source_hash
    return False, source_hash


def assess_process_access(
    *,
    source_path: str,
    access_mask: int,
    allowlist: AllowlistSettings,
    fingerprints: FileFingerprintCache,
) -> AccessAssessment | None:
    labels = access_labels(access_mask)
    if not labels:
        return None

    trusted, source_hash = source_is_allowlisted(
        source_path,
        allowlist=allowlist,
        fingerprints=fingerprints,
    )
    if trusted:
        return AccessAssessment(
            category="trusted_access",
            strength=0.0,
            reliability=1.0,
            reason=(
                "source SHA-256 is explicitly allowlisted"
                if source_hash
                else "source path is explicitly allowlisted"
            ),
            access_labels=labels,
            trusted=True,
            source_sha256=source_hash,
        )

    if access_mask & _TAMPER_MASK:
        return AccessAssessment(
            category="process_tamper",
            strength=1.0,
            reliability=0.97,
            reason="untrusted process requested memory modification or remote-thread rights",
            access_labels=labels,
            source_sha256=source_hash,
        )
    if access_mask & PROCESS_VM_READ:
        return AccessAssessment(
            category="memory_read",
            strength=0.88,
            reliability=0.95,
            reason="untrusted process requested permission to read game memory",
            access_labels=labels,
            source_sha256=source_hash,
        )
    if access_mask & PROCESS_DUP_HANDLE:
        return AccessAssessment(
            category="handle_duplicate",
            strength=0.62,
            reliability=0.85,
            reason="untrusted process requested handle-duplication access",
            access_labels=labels,
            source_sha256=source_hash,
        )
    return None
