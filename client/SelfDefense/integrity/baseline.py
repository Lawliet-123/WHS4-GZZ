"""Explicit release baselines. SHA-256 pinning is not publisher authentication."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

MAX_FILES = 512
MAX_BASELINE_BYTES = 512 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
CODE_SUFFIXES = frozenset({".py", ".pyw", ".exe", ".dll", ".pyd", ".lua"})
EXCLUDED_DIRS = frozenset({".git", ".venv", "venv", "__pycache__", "logs", "docs", "tests",
                           "examples", "telemetry-outbox", "outbox", "sessions", "replay_exports"})
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


class IntegrityError(ValueError):
    """Only public error codes; do not transmit arbitrary OS errors or absolute paths."""


def check_digest(value):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise IntegrityError("INVALID_BASELINE_PIN")
    return value


def relative_name(value):
    if (not isinstance(value, str) or not value or len(value) > 240
            or "\\" in value or any(ord(c) < 32 for c in value)
            or any(c in value for c in ':*?"<>|') or value.startswith("/")):
        raise IntegrityError("INVALID_FILE_PATH")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise IntegrityError("INVALID_FILE_PATH") from None
    parts = value.split("/")
    if any(not p or p in {".", ".."} or p.endswith((".", " "))
           or p.split(".")[0].upper() in _RESERVED for p in parts):
        raise IntegrityError("INVALID_FILE_PATH")
    if PurePosixPath(value).as_posix() != value:
        raise IntegrityError("INVALID_FILE_PATH")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IntegrityError("DUPLICATE_BASELINE_KEY")
        result[key] = value
    return result


def validate_manifest(value):
    if (type(value) is not dict or set(value) != {"schema_version", "release_id", "algorithm", "scope_dirs", "files"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["algorithm"] != "sha256"):
        raise IntegrityError("INVALID_BASELINE_SCHEMA")
    release = value["release_id"]
    if not isinstance(release, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", release):
        raise IntegrityError("INVALID_RELEASE_ID")
    scopes = value["scope_dirs"]
    if type(scopes) is not list or len(scopes) > 32:
        raise IntegrityError("INVALID_SCOPE")
    checked = [relative_name(s).casefold() for s in scopes]
    if any(a == b or a.startswith(b + "/") or b.startswith(a + "/")
           for i, a in enumerate(checked) for b in checked[i + 1:]):
        raise IntegrityError("OVERLAPPING_SCOPE")
    files = value["files"]
    if type(files) is not list or not 1 <= len(files) <= MAX_FILES:
        raise IntegrityError("INVALID_FILE_COUNT")
    names, total = set(), 0
    for item in files:
        if type(item) is not dict or set(item) != {"path", "size", "sha256"}:
            raise IntegrityError("INVALID_FILE_ENTRY")
        path = relative_name(item["path"])
        if path.casefold() in names:
            raise IntegrityError("DUPLICATE_FILE_PATH")
        names.add(path.casefold())
        if type(item["size"]) is not int or not 0 <= item["size"] <= MAX_FILE_BYTES:
            raise IntegrityError("INVALID_FILE_SIZE")
        total += item["size"]
        if not isinstance(item["sha256"], str) or not _DIGEST.fullmatch(item["sha256"]):
            raise IntegrityError("INVALID_FILE_DIGEST")
    if total > MAX_TOTAL_BYTES:
        raise IntegrityError("BASELINE_TOTAL_TOO_LARGE")
    return value


def _reject_link(info):
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise IntegrityError("REPARSE_POINT_NOT_ALLOWED")


def checked_path(root, name):
    """Reject static path escapes/reparse points. Not an atomic filesystem sandbox."""
    relative_name(name)
    if not root.is_absolute():
        raise IntegrityError("ROOT_NOT_ABSOLUTE")
    info = root.lstat()
    _reject_link(info)
    if not stat.S_ISDIR(info.st_mode):
        raise IntegrityError("ROOT_NOT_DIRECTORY")
    path = root
    for part in name.split("/"):
        path = path / part
        _reject_link(path.lstat())
    if not path.resolve().is_relative_to(root.resolve()):
        raise IntegrityError("PATH_OUTSIDE_ROOT")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise IntegrityError("NOT_REGULAR_FILE")
    if info.st_nlink > 1:
        raise IntegrityError("HARD_LINK_NOT_ALLOWED")
    return path


def _identity(info):
    # Windows Python versions can expose different ctime semantics in stat vs
    # fstat. Compare ctime only within the same API below, never across APIs.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def fingerprint(root, name, *, max_bytes=MAX_FILE_BYTES):
    path = checked_path(root, name)
    before = path.stat()
    if before.st_size > min(MAX_FILE_BYTES, max_bytes):
        raise IntegrityError("FILE_TOO_LARGE")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _identity(opened) != _identity(before) or not stat.S_ISREG(opened.st_mode):
            raise IntegrityError("FILE_CHANGED_DURING_READ")
        total = 0
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_FILE_BYTES or total > before.st_size:
                raise IntegrityError("FILE_CHANGED_DURING_READ")
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    final = checked_path(root, name).stat()
    if (total != before.st_size or _identity(before) != _identity(after)
            or _identity(before) != _identity(final)
            or opened.st_ctime_ns != after.st_ctime_ns
            or before.st_ctime_ns != final.st_ctime_ns):
        raise IntegrityError("FILE_CHANGED_DURING_READ")
    return {"path": name, "size": total, "sha256": digest.hexdigest()}


def discover_files(root, scopes):
    """Enumerate only selected code scopes; no executable is imported or executed."""
    found, visited = [], 0
    for scope in scopes:
        relative_name(scope)
        start = root
        for part in scope.split("/"):
            start = start / part
            info = start.lstat()
            _reject_link(info)
            if not stat.S_ISDIR(info.st_mode):
                raise IntegrityError("SCOPE_NOT_DIRECTORY")
        pending = [start]
        while pending:
            folder = pending.pop()
            _reject_link(folder.lstat())
            with os.scandir(folder) as children:
                for child in children:
                    visited += 1
                    if visited > 50000:
                        raise IntegrityError("SCOPE_TOO_LARGE")
                    info = child.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode) and child.name.casefold() in EXCLUDED_DIRS:
                        continue
                    _reject_link(info)
                    path = Path(child.path)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                    elif path.suffix.casefold() in CODE_SUFFIXES:
                        found.append(relative_name(path.relative_to(root).as_posix()))
                        if len(found) > MAX_FILES:
                            raise IntegrityError("SCOPE_TOO_MANY_FILES")
    return sorted(found)


def error_code(exc):
    if isinstance(exc, IntegrityError):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return "FILE_MISSING"
    if isinstance(exc, PermissionError):
        return "ACCESS_DENIED"
    return "FILE_READ_ERROR"


def load_baseline(path, expected_sha256):
    check_digest(expected_sha256)
    try:
        info = path.lstat()
        _reject_link(info)
        if not stat.S_ISREG(info.st_mode):
            raise IntegrityError("BASELINE_NOT_REGULAR_FILE")
        with path.open("rb") as stream:
            data = stream.read(MAX_BASELINE_BYTES + 1)
        if len(data) > MAX_BASELINE_BYTES:
            raise IntegrityError("BASELINE_TOO_LARGE")
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise IntegrityError("BASELINE_PIN_MISMATCH")
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
        return validate_manifest(value)
    except IntegrityError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise IntegrityError("INVALID_BASELINE_JSON") from None
    except OSError:
        raise IntegrityError("BASELINE_UNREADABLE") from None


def make_baseline(root, names, release_id, *, scope_dirs=()):
    # Check the complete input before reading any file; never recurse through a PC.
    skeleton = {"schema_version": 1, "release_id": release_id, "algorithm": "sha256", "scope_dirs": list(scope_dirs),
                "files": [{"path": n, "size": 0, "sha256": "0" * 64} for n in names]}
    validate_manifest(skeleton)
    files, remaining = [], MAX_TOTAL_BYTES
    for name in sorted(names):
        entry = fingerprint(root, name, max_bytes=remaining)
        files.append(entry)
        remaining -= entry["size"]
    skeleton["files"] = files
    validate_manifest(skeleton)
    return (json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
