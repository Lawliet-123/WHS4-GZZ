"""Local-disk JSONL sink with a durable idempotency/recovery ledger.

All writers for one root must use this class. Do not edit/rotate its files behind
its back; JSONL, the ledger, and their backup form one storage unit.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ._locking import FileLock
from ._sqlite import database
from .config import WriterConfig
from .errors import IdempotencyConflict, StorageError
from .schema import encode_event, validate_event_id


@dataclass(frozen=True)
class WriteReceipt:
    event_id: str
    status: str
    sequence: int
    path: Path

    def to_ack(self) -> dict:
        return {"event_id": self.event_id, "status": self.status}


@dataclass(frozen=True)
class StoredDetection:
    sequence: int
    event_id: str
    result: dict


class DetectionSink(Protocol):
    def write_detection(self, result, *, event_id: str) -> WriteReceipt: ...


class DetectionWriter:
    """Synchronous disk API: use a sync route/thread pool, not an async event loop."""
    def __init__(self, config: WriterConfig):
        self.config, self.root = config, config.root
        self._index = self.root / "writer-index.sqlite3"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock():
                with database(self._index) as db:
                    db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    version = db.execute("SELECT value FROM metadata WHERE key='writer_version'").fetchone()
                    if version and version[0] != "1":
                        raise StorageError("unsupported writer storage version; explicit migration required")
                    db.execute("INSERT OR IGNORE INTO metadata VALUES ('writer_version', '1')")
                    db.execute("""CREATE TABLE IF NOT EXISTS stored (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT UNIQUE NOT NULL, digest TEXT NOT NULL, payload BLOB NOT NULL,
                        relative_path TEXT NOT NULL, byte_offset INTEGER NOT NULL, state TEXT NOT NULL)""")
                self._recover()
        except OSError as exc:
            raise StorageError("cannot initialize detection storage") from exc

    def _lock(self):
        return FileLock(self.root / "writer.lock", timeout=self.config.lock_timeout_seconds)

    def _path(self, relative: str) -> Path:
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root):
            raise StorageError("storage path escaped configured root")
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.is_symlink():
                raise StorageError("symlinks are not allowed inside detection storage")
        return path

    def _append_pending(self, row) -> None:
        """Complete ONLY a matching unfinished tail, or fail closed on outside edits."""
        path = self._path(row["relative_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        expected = bytes(row["payload"]) + b"\n"
        offset = row["byte_offset"]
        if not path.exists():
            if offset != 0:
                raise StorageError("previous JSONL data is missing")
            path.touch(exist_ok=False)
        with path.open("r+b") as stream:
            size = os.fstat(stream.fileno()).st_size
            if not offset <= size <= offset + len(expected):
                raise StorageError("unexpected JSONL tail; manual recovery required")
            stream.seek(offset)
            tail = stream.read(len(expected))
            if not expected.startswith(tail):
                raise StorageError("JSONL tail differs from pending record; manual recovery required")
            stream.seek(size)
            stream.write(expected[len(tail):])
            stream.flush()
            os.fsync(stream.fileno())
        with database(self._index) as db:
            db.execute("UPDATE stored SET state='committed' WHERE event_id=?", (row["event_id"],))

    def _recover(self) -> None:
        with database(self._index) as db:
            rows = db.execute("SELECT * FROM stored WHERE state='pending' ORDER BY sequence").fetchall()
        for row in rows:
            self._append_pending(row)

    def _verify_committed(self, row) -> Path:
        path = self._path(row["relative_path"])
        expected = bytes(row["payload"]) + b"\n"
        with path.open("rb") as stream:
            stream.seek(row["byte_offset"])
            if stream.read(len(expected)) != expected:
                raise StorageError("stored JSONL no longer matches its receipt")
        return path

    def write_detection(self, result, *, event_id: str) -> WriteReceipt:
        key = validate_event_id(event_id)
        payload = encode_event(result, max_bytes=self.config.max_event_bytes)
        event = json.loads(payload)
        digest = hashlib.sha256(payload).hexdigest()
        relative = f"{event['module']}/{event['session_id']}/{event['player_id']}.jsonl"
        try:
            with self._lock():
                self._recover()
                with database(self._index) as db:
                    existing = db.execute("SELECT * FROM stored WHERE event_id=?", (key,)).fetchone()
                if existing:
                    if existing["digest"] != digest or bytes(existing["payload"]) != payload:
                        raise IdempotencyConflict("event_id is already associated with different content")
                    path = self._verify_committed(existing)
                    return WriteReceipt(key, "duplicate", existing["sequence"], path)
                path = self._path(relative)
                offset = path.stat().st_size if path.exists() else 0
                with database(self._index) as db:
                    previous = db.execute("SELECT byte_offset, LENGTH(payload) FROM stored WHERE relative_path=? ORDER BY sequence DESC LIMIT 1", (relative,)).fetchone()
                    expected_offset = previous[0] + previous[1] + 1 if previous else 0
                    if offset != expected_offset:
                        raise StorageError("JSONL was changed outside the writer; manual recovery required")
                    db.execute("INSERT INTO stored(event_id,digest,payload,relative_path,byte_offset,state) VALUES(?,?,?,?,?,'pending')",
                               (key, digest, payload, relative, offset))
                    row = db.execute("SELECT * FROM stored WHERE event_id=?", (key,)).fetchone()
                self._append_pending(row)
                return WriteReceipt(key, "stored", row["sequence"], path)
        except OSError as exc:
            raise StorageError("detection file operation failed; do not acknowledge success") from exc

    def iter_stored(self, *, after_sequence: int = 0, limit: int = 1000) -> list[StoredDetection]:
        """Durable ordered feed for scoring recovery; cursor belongs to the consumer."""
        if type(after_sequence) is not int or after_sequence < 0 or type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("invalid feed cursor or limit")
        try:
            with self._lock():
                self._recover()
                with database(self._index) as db:
                    rows = db.execute("SELECT * FROM stored WHERE state='committed' AND sequence>? ORDER BY sequence LIMIT ?",
                                      (after_sequence, limit)).fetchall()
                results = []
                for row in rows:
                    self._verify_committed(row)
                    results.append(StoredDetection(row["sequence"], row["event_id"], json.loads(bytes(row["payload"]))))
                return results
        except OSError as exc:
            raise StorageError("cannot read stored detection feed") from exc
