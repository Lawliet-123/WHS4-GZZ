"""Durable idempotency and current-state storage for server-side scoring.

This layer intentionally does not choose cheat thresholds or aggregate detector
scores.  It establishes the safety properties required by the shared telemetry
handoff first:

* the same ``event_id`` is reflected at most once;
* repeated detector samples replace current state instead of accumulating;
* late/out-of-order samples do not overwrite a newer module state;
* recovery progress is stored separately from live request processing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from shared.schema import encode_event, validate_event_id


@dataclass(frozen=True)
class ProcessReceipt:
    event_id: str
    sequence: int
    status: str
    state_updated: bool


@dataclass(frozen=True)
class ModuleState:
    session_id: str
    player_id: str
    module: str
    timestamp_ms: int
    sequence: int
    event_id: str
    raw_score: float
    evidence: dict[str, Any]
    reasons: list[str]


class ScoringStore:
    """SQLite-backed scoring state for a single server deployment."""

    STORAGE_VERSION = "1"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _initialize(self) -> None:
        try:
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS metadata ("
                    "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                row = db.execute(
                    "SELECT value FROM metadata WHERE key='scoring_version'"
                ).fetchone()
                if row is not None and row["value"] != self.STORAGE_VERSION:
                    raise RuntimeError(
                        "unsupported scoring storage version; explicit migration required"
                    )
                db.execute(
                    "INSERT OR IGNORE INTO metadata(key, value) VALUES('scoring_version', ?)",
                    (self.STORAGE_VERSION,),
                )
                db.execute(
                    "INSERT OR IGNORE INTO metadata(key, value) VALUES('recovery_cursor', '0')"
                )
                db.execute(
                    """CREATE TABLE IF NOT EXISTS processed_events (
                        event_id TEXT PRIMARY KEY,
                        sequence INTEGER NOT NULL UNIQUE,
                        digest TEXT NOT NULL
                    )"""
                )
                db.execute(
                    """CREATE TABLE IF NOT EXISTS latest_state (
                        session_id TEXT NOT NULL,
                        player_id TEXT NOT NULL,
                        module TEXT NOT NULL,
                        timestamp_ms INTEGER NOT NULL,
                        sequence INTEGER NOT NULL,
                        event_id TEXT NOT NULL,
                        raw_score REAL NOT NULL,
                        evidence_json TEXT NOT NULL,
                        reasons_json TEXT NOT NULL,
                        PRIMARY KEY(session_id, player_id, module)
                    )"""
                )
                db.commit()
        except sqlite3.Error as exc:
            raise RuntimeError("cannot initialize scoring storage") from exc

    @staticmethod
    def _canonical_event(result: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        payload = encode_event(result)
        event = json.loads(payload)
        digest = hashlib.sha256(payload).hexdigest()
        return event, digest

    def process_event(
        self,
        result: Mapping[str, Any],
        *,
        event_id: str,
        sequence: int,
    ) -> ProcessReceipt:
        """Reflect one stored detection into scoring state exactly once per event ID."""
        event_id = validate_event_id(event_id)
        if type(sequence) is not int or sequence <= 0:
            raise ValueError("sequence must be a positive integer")

        event, digest = self._canonical_event(result)

        try:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT sequence, digest FROM processed_events WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    if existing["digest"] != digest or existing["sequence"] != sequence:
                        raise RuntimeError(
                            "event_id is already associated with different scoring input"
                        )
                    db.commit()
                    return ProcessReceipt(event_id, sequence, "duplicate", False)

                sequence_owner = db.execute(
                    "SELECT event_id FROM processed_events WHERE sequence=?",
                    (sequence,),
                ).fetchone()
                if sequence_owner is not None:
                    raise RuntimeError("sequence is already associated with another event_id")

                db.execute(
                    "INSERT INTO processed_events(event_id, sequence, digest) VALUES(?,?,?)",
                    (event_id, sequence, digest),
                )

                key = (event["session_id"], event["player_id"], event["module"])
                current = db.execute(
                    """SELECT timestamp_ms, sequence
                       FROM latest_state
                       WHERE session_id=? AND player_id=? AND module=?""",
                    key,
                ).fetchone()

                should_update = (
                    current is None
                    or event["timestamp_ms"] > current["timestamp_ms"]
                    or (
                        event["timestamp_ms"] == current["timestamp_ms"]
                        and sequence > current["sequence"]
                    )
                )

                if should_update:
                    db.execute(
                        """INSERT INTO latest_state(
                               session_id, player_id, module, timestamp_ms, sequence,
                               event_id, raw_score, evidence_json, reasons_json
                           ) VALUES(?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(session_id, player_id, module) DO UPDATE SET
                               timestamp_ms=excluded.timestamp_ms,
                               sequence=excluded.sequence,
                               event_id=excluded.event_id,
                               raw_score=excluded.raw_score,
                               evidence_json=excluded.evidence_json,
                               reasons_json=excluded.reasons_json""",
                        (
                            event["session_id"],
                            event["player_id"],
                            event["module"],
                            event["timestamp_ms"],
                            sequence,
                            event_id,
                            float(event["raw_score"]),
                            json.dumps(
                                event["evidence"],
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            json.dumps(
                                event["reasons"],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        ),
                    )

                db.commit()
                return ProcessReceipt(event_id, sequence, "processed", should_update)
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise RuntimeError("scoring storage operation failed") from exc

    def get_module_state(
        self, session_id: str, player_id: str, module: str
    ) -> ModuleState | None:
        try:
            with closing(self._connect()) as db:
                row = db.execute(
                    """SELECT * FROM latest_state
                       WHERE session_id=? AND player_id=? AND module=?""",
                    (session_id, player_id, module),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeError("cannot read scoring state") from exc
        return self._row_to_state(row) if row is not None else None

    def get_player_snapshot(self, session_id: str, player_id: str) -> list[ModuleState]:
        try:
            with closing(self._connect()) as db:
                rows = db.execute(
                    """SELECT * FROM latest_state
                       WHERE session_id=? AND player_id=?
                       ORDER BY module""",
                    (session_id, player_id),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError("cannot read scoring snapshot") from exc
        return [self._row_to_state(row) for row in rows]

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> ModuleState:
        return ModuleState(
            session_id=row["session_id"],
            player_id=row["player_id"],
            module=row["module"],
            timestamp_ms=row["timestamp_ms"],
            sequence=row["sequence"],
            event_id=row["event_id"],
            raw_score=row["raw_score"],
            evidence=json.loads(row["evidence_json"]),
            reasons=json.loads(row["reasons_json"]),
        )

    def get_recovery_cursor(self) -> int:
        try:
            with closing(self._connect()) as db:
                row = db.execute(
                    "SELECT value FROM metadata WHERE key='recovery_cursor'"
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeError("cannot read scoring recovery cursor") from exc
        return int(row["value"]) if row is not None else 0

    def advance_recovery_cursor(self, sequence: int) -> None:
        """Advance the feed cursor only after that sequence has been processed.

        Live ``process_event`` calls deliberately do not move this cursor.  That
        prevents a concurrently processed newer request from skipping an older
        stored record during crash recovery.
        """
        if type(sequence) is not int or sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        try:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                current_row = db.execute(
                    "SELECT value FROM metadata WHERE key='recovery_cursor'"
                ).fetchone()
                current = int(current_row["value"]) if current_row is not None else 0
                if sequence < current:
                    db.commit()
                    return
                processed = db.execute(
                    "SELECT 1 FROM processed_events WHERE sequence=?", (sequence,)
                ).fetchone()
                if processed is None:
                    raise RuntimeError(
                        "cannot advance recovery cursor past an unprocessed sequence"
                    )
                db.execute(
                    "UPDATE metadata SET value=? WHERE key='recovery_cursor'",
                    (str(sequence),),
                )
                db.commit()
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise RuntimeError("cannot update scoring recovery cursor") from exc
