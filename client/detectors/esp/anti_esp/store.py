"""Thread-safe SQLite persistence for evidence events."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any
from uuid import uuid4

from .models import EvidenceEvent
_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_events (
    event_id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL,
    session_id TEXT NOT NULL,
    subject_id TEXT,
    category TEXT NOT NULL,
    source TEXT NOT NULL,
    strength REAL NOT NULL,
    reliability REAL NOT NULL,
    reason TEXT NOT NULL,
    dedup_key TEXT,
    details_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_timestamp
    ON evidence_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_session_timestamp
    ON evidence_events(session_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_category_timestamp
    ON evidence_events(category, timestamp DESC);
CREATE TABLE IF NOT EXISTS team_event_outbox (
    outbox_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    delivered_at REAL
);
CREATE INDEX IF NOT EXISTS idx_team_outbox_pending
    ON team_event_outbox(session_id, delivered_at, created_at);
"""


class SQLiteEvidenceStore:
    """Persist and query :class:`EvidenceEvent` values.

    A single connection is protected by an ``RLock`` and opened with
    ``check_same_thread=False``.  This makes one store instance safe to share
    between sensor threads.  SQLite still serializes writes across independent
    processes and store instances.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = (
            ":memory:" if str(path) == ":memory:" else str(Path(path).expanduser())
        )
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 10000")
            if self.path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("evidence store is closed")

    @staticmethod
    def _row_values(event: EvidenceEvent) -> tuple[Any, ...]:
        return (
            event.event_id,
            event.timestamp,
            event.session_id,
            event.subject_id,
            event.category,
            event.source,
            event.strength,
            event.reliability,
            event.reason,
            event.dedup_key,
            json.dumps(
                event.details,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> EvidenceEvent:
        return EvidenceEvent(
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            session_id=row["session_id"],
            subject_id=row["subject_id"],
            category=row["category"],
            source=row["source"],
            strength=row["strength"],
            reliability=row["reliability"],
            reason=row["reason"],
            dedup_key=row["dedup_key"],
            details=json.loads(row["details_json"]),
        )

    def append(self, event: EvidenceEvent) -> bool:
        """Store an event, returning ``False`` when its ID already exists."""

        if not isinstance(event, EvidenceEvent):
            raise TypeError("event must be an EvidenceEvent")
        sql = """
            INSERT OR IGNORE INTO evidence_events (
                event_id, timestamp, session_id, subject_id, category, source,
                strength, reliability, reason, dedup_key, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            self._ensure_open()
            cursor = self._connection.execute(sql, self._row_values(event))
            self._connection.commit()
            return cursor.rowcount == 1

    save_event = append

    def append_many(self, events: Iterable[EvidenceEvent]) -> int:
        """Atomically store multiple events and return the inserted count."""

        materialized = list(events)
        if any(not isinstance(event, EvidenceEvent) for event in materialized):
            raise TypeError("all events must be EvidenceEvent instances")
        if not materialized:
            return 0
        sql = """
            INSERT OR IGNORE INTO evidence_events (
                event_id, timestamp, session_id, subject_id, category, source,
                strength, reliability, reason, dedup_key, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            self._ensure_open()
            before = self._connection.total_changes
            try:
                self._connection.executemany(
                    sql, (self._row_values(event) for event in materialized)
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            return self._connection.total_changes - before

    def append_many_with_outbox(
        self,
        events: Iterable[EvidenceEvent],
        outbox_factory: Callable[
            [tuple[EvidenceEvent, ...]], tuple[str, str] | None
        ],
    ) -> tuple[tuple[EvidenceEvent, ...], str | None]:
        """Atomically store evidence and a derived team-event outbox record.

        ``outbox_factory`` is called with only the evidence inserted by this
        transaction and must be pure: it returns ``(session_id, payload_json)``
        and performs no file or network I/O.  The JSONL sink is flushed only
        after SQLite commits, so a database commit failure cannot leave a
        public event whose evidence was rolled back.
        """

        materialized = tuple(events)
        if any(not isinstance(event, EvidenceEvent) for event in materialized):
            raise TypeError("all events must be EvidenceEvent instances")
        if not callable(outbox_factory):
            raise TypeError("outbox_factory must be callable")
        sql = """
            INSERT OR IGNORE INTO evidence_events (
                event_id, timestamp, session_id, subject_id, category, source,
                strength, reliability, reason, dedup_key, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            self._ensure_open()
            inserted: list[EvidenceEvent] = []
            outbox_id: str | None = None
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                for event in materialized:
                    cursor = self._connection.execute(sql, self._row_values(event))
                    if cursor.rowcount == 1:
                        inserted.append(event)
                outbox = outbox_factory(tuple(inserted))
                if outbox is not None:
                    session_id, payload_json = outbox
                    if not isinstance(session_id, str) or not session_id.strip():
                        raise ValueError("outbox session_id must be non-empty")
                    if not isinstance(payload_json, str) or not payload_json.strip():
                        raise ValueError("outbox payload_json must be non-empty")
                    decoded = json.loads(payload_json)
                    if not isinstance(decoded, dict):
                        raise ValueError("outbox payload_json must contain an object")
                    if decoded.get("session_id") != session_id:
                        raise ValueError("outbox payload session_id mismatch")
                    if any(event.session_id != session_id for event in inserted):
                        raise ValueError("outbox and evidence session_id mismatch")
                    digest_input = f"{session_id}\0{payload_json}".encode("utf-8")
                    outbox_id = sha256(digest_input).hexdigest()
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO team_event_outbox (
                            outbox_id, session_id, payload_json, created_at,
                            delivered_at
                        ) VALUES (?, ?, ?, strftime('%s','now'), NULL)
                        """,
                        (outbox_id, session_id, payload_json),
                    )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return tuple(inserted), outbox_id

    def pending_team_events(
        self, *, session_id: str, limit: int = 100
    ) -> list[tuple[str, str]]:
        """Return undelivered team payloads in deterministic creation order."""

        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be non-empty")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT outbox_id, payload_json
                FROM team_event_outbox
                WHERE session_id = ? AND delivered_at IS NULL
                ORDER BY created_at ASC, outbox_id ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [(str(row["outbox_id"]), str(row["payload_json"])) for row in rows]

    def mark_team_event_delivered(self, outbox_id: str) -> bool:
        """Mark one successfully written outbox item as delivered."""

        if not isinstance(outbox_id, str) or not outbox_id.strip():
            raise ValueError("outbox_id must be non-empty")
        with self._lock:
            self._ensure_open()
            cursor = self._connection.execute(
                """
                UPDATE team_event_outbox
                SET delivered_at = strftime('%s','now')
                WHERE outbox_id = ? AND delivered_at IS NULL
                """,
                (outbox_id,),
            )
            self._connection.commit()
            return cursor.rowcount == 1

    def pending_team_event_count(self, *, session_id: str) -> int:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS pending_count
                FROM team_event_outbox
                WHERE session_id = ? AND delivered_at IS NULL
                """,
                (session_id,),
            ).fetchone()
        return int(row["pending_count"])

    def _query(
        self,
        *,
        limit: int | None,
        since: float | None,
        category: str | None,
        session_id: str | None,
        subject_id: str | None,
        ascending: bool,
    ) -> list[EvidenceEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(float(since))
        if category is not None:
            clauses.append("category = ?")
            params.append(category)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if subject_id is not None:
            clauses.append("subject_id = ?")
            params.append(subject_id)

        sql = "SELECT * FROM evidence_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        direction = "ASC" if ascending else "DESC"
        sql += f" ORDER BY timestamp {direction}, event_id {direction}"
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValueError("limit must be a positive integer or None")
            sql += " LIMIT ?"
            params.append(limit)

        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(sql, params).fetchall()
        return [self._from_row(row) for row in rows]

    def recent(
        self,
        limit: int = 100,
        *,
        since: float | None = None,
        category: str | None = None,
        session_id: str | None = None,
        subject_id: str | None = None,
    ) -> list[EvidenceEvent]:
        """Return the newest matching events first."""

        return self._query(
            limit=limit,
            since=since,
            category=category,
            session_id=session_id,
            subject_id=subject_id,
            ascending=False,
        )

    recent_events = recent

    def count(self) -> int:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT COUNT(*) AS event_count FROM evidence_events"
            ).fetchone()
            return int(row["event_count"])

    def export_jsonl(
        self,
        destination: str | Path,
        *,
        since: float | None = None,
        category: str | None = None,
        session_id: str | None = None,
        subject_id: str | None = None,
    ) -> int:
        """Atomically export matching events in chronological JSONL order."""

        events = self._query(
            limit=None,
            since=since,
            category=category,
            session_id=session_id,
            subject_id=subject_id,
            ascending=True,
        )
        output = Path(destination).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                for event in events:
                    handle.write(event.to_json())
                    handle.write("\n")
            temporary.replace(output)
        finally:
            if temporary.exists():
                temporary.unlink()
        return len(events)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> "SQLiteEvidenceStore":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
