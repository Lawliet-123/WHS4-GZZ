"""Durable heartbeat history and last accepted sequence; separate from scoring."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .heartbeat_schema import canonical_heartbeat


class HeartbeatConflict(ValueError):
    """The sequence is stale, or the last sequence has different content."""


class HeartbeatStorageError(RuntimeError):
    """The durable heartbeat database is temporarily unavailable."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class HeartbeatReceipt:
    session_id: str
    client_id: str
    sequence: int
    duplicate: bool

    def to_ack(self) -> dict[str, Any]:
        return {
            "accepted": True, "session_id": self.session_id,
            "client_id": self.client_id, "sequence": self.sequence,
        }


class HeartbeatStore:
    """One SQLite transaction serializes sequence checking and durable storage.

    Each operation opens its own connection so FastAPI worker threads and
    multiple application instances on the same machine can use the same file.
    Calls to accept() must receive an already validated heartbeat.
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], str] = _utc_now):
        if str(path) == ":memory:":
            raise ValueError("heartbeat storage requires a persistent file")
        self.path = Path(path)
        self.clock = clock
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS heartbeats (
                        session_id TEXT NOT NULL,
                        client_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL CHECK(sequence >= 1),
                        received_at_utc TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        PRIMARY KEY (session_id, client_id, sequence)
                    )
                """)
        except OSError as exc:
            raise HeartbeatStorageError("heartbeat storage cannot be initialized") from exc

    @contextmanager
    def _connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            yield db
        except sqlite3.Error as exc:
            raise HeartbeatStorageError("heartbeat storage is unavailable") from exc
        finally:
            if db is not None:
                db.close()  # Closing an unfinished transaction rolls it back.

    def accept(self, payload: dict[str, Any]) -> HeartbeatReceipt:
        session_id, client_id, sequence = (
            payload["session_id"], payload["client_id"], payload["sequence"]
        )
        canonical = canonical_heartbeat(payload)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            last = db.execute(
                "SELECT sequence, payload FROM heartbeats WHERE session_id=? AND client_id=? "
                "ORDER BY sequence DESC LIMIT 1", (session_id, client_id),
            ).fetchone()
            if last is not None:
                if sequence < last["sequence"]:
                    raise HeartbeatConflict("heartbeat sequence is older than the last accepted sequence")
                if sequence == last["sequence"]:
                    if canonical != last["payload"]:
                        raise HeartbeatConflict("heartbeat sequence conflicts with existing content")
                    db.commit()
                    # Duplicate ACKs do not update server liveness time.
                    return HeartbeatReceipt(session_id, client_id, sequence, True)
            db.execute(
                "INSERT INTO heartbeats VALUES (?, ?, ?, ?, ?)",
                (session_id, client_id, sequence, self.clock(), canonical),
            )
            db.commit()
        return HeartbeatReceipt(session_id, client_id, sequence, False)

    def latest(self, session_id: str, client_id: str) -> dict[str, Any] | None:
        """Return accepted server time and state for C/Dashboard integration."""
        with self._connect() as db:
            last = db.execute(
                "SELECT sequence, received_at_utc, payload FROM heartbeats "
                "WHERE session_id=? AND client_id=? ORDER BY sequence DESC LIMIT 1",
                (session_id, client_id),
            ).fetchone()
        if last is None:
            return None
        return {
            "sequence": last["sequence"], "received_at_utc": last["received_at_utc"],
            "payload": json.loads(last["payload"]),
        }
