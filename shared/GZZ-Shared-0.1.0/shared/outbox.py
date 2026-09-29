"""Durable bounded queue. Contains payloads and metadata, never access tokens."""
from __future__ import annotations

import time
from dataclasses import dataclass

from ._sqlite import database
from .config import ClientConfig
from .errors import ConfigurationError, EventAlreadyFailedError, IdempotencyConflict, QueueFullError


@dataclass(frozen=True)
class PendingEvent:
    event_id: str
    payload: bytes
    attempts: int


class SQLiteOutbox:
    """One sending worker owns the file; producers can enqueue from multiple threads."""
    def __init__(self, config: ClientConfig):
        self.config, self.path = config, config.outbox_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with database(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            for key, value in (("outbox_version", "1"), ("endpoint", config.endpoint)):
                existing = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
                if existing and existing[0] != value:
                    raise ConfigurationError("outbox version or destination changed; use a separate outbox or an explicit migration")
                db.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (key, value))
            db.execute("""CREATE TABLE IF NOT EXISTS pending (
                event_id TEXT PRIMARY KEY, payload BLOB NOT NULL,
                state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_at REAL NOT NULL, created_at REAL NOT NULL, last_error TEXT)""")

    def enqueue(self, event_id: str, payload: bytes) -> None:
        with database(self.path) as db:
            existing = db.execute("SELECT payload, state FROM pending WHERE event_id=?", (event_id,)).fetchone()
            if existing:
                if bytes(existing[0]) != payload:
                    raise IdempotencyConflict("event_id already has a different queued payload")
                if existing[1] == "failed":
                    raise EventAlreadyFailedError("event is retained as failed; use retry_failed after fixing the cause")
                return
            count, size = db.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)), 0) FROM pending").fetchone()
            if count >= self.config.max_queue_events or size + len(payload) > self.config.max_queue_bytes:
                raise QueueFullError("outbox is full; result was not queued and existing records were not deleted")
            now = time.time()
            db.execute("INSERT INTO pending VALUES (?, ?, 'pending', 0, ?, ?, NULL)",
                       (event_id, payload, now, now))

    def next_due(self) -> PendingEvent | None:
        with database(self.path) as db:
            row = db.execute("SELECT event_id, payload, attempts FROM pending WHERE state='pending' AND next_at<=? ORDER BY created_at, rowid LIMIT 1", (time.time(),)).fetchone()
            return PendingEvent(row[0], bytes(row[1]), row[2]) if row else None

    def begin_attempt(self, event_id: str) -> None:
        with database(self.path) as db:
            db.execute("UPDATE pending SET attempts=attempts+1 WHERE event_id=?", (event_id,))

    def acknowledge(self, event_id: str) -> None:
        with database(self.path) as db:
            db.execute("DELETE FROM pending WHERE event_id=?", (event_id,))

    def retry_later(self, event_id: str, delay: float, error: str) -> None:
        with database(self.path) as db:
            db.execute("UPDATE pending SET next_at=?, last_error=? WHERE event_id=?", (time.time() + delay, error, event_id))

    def fail(self, event_id: str, error: str) -> None:
        with database(self.path) as db:
            db.execute("UPDATE pending SET state='failed', last_error=? WHERE event_id=?", (error, event_id))

    def retry_failed(self, event_id: str | None = None) -> int:
        with database(self.path) as db:
            sql = "UPDATE pending SET state='pending', attempts=0, next_at=?, last_error=NULL WHERE state='failed'"
            args = [time.time()]
            if event_id is not None:
                sql += " AND event_id=?"
                args.append(event_id)
            return db.execute(sql, args).rowcount

    def counts(self) -> tuple[int, int, int]:
        with database(self.path) as db:
            row = db.execute("SELECT COALESCE(SUM(state='pending'), 0), COALESCE(SUM(state='failed'), 0), COALESCE(SUM(LENGTH(payload)), 0) FROM pending").fetchone()
            return tuple(row)

    def failures(self, limit: int = 100) -> list[dict]:
        with database(self.path) as db:
            return [dict(row) for row in db.execute("SELECT event_id, attempts, last_error FROM pending WHERE state='failed' ORDER BY created_at LIMIT ?", (limit,))]
