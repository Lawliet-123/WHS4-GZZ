"""Receiver-facing adapter for server-side scoring state.

Threshold normalization and final cheat policy are intentionally kept out of
this first integration step.  The current contract establishes durable,
idempotent processing and per-module current state; policy can be added on top
without changing the receiver API.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Mapping

from .storage import ModuleState, ProcessReceipt, ScoringStore

_DEFAULT_DB = Path(__file__).resolve().parents[1] / "logs" / "scoring" / "scoring.sqlite3"
_store: ScoringStore | None = None
_store_lock = threading.Lock()


def configure_scoring(path: str | Path | None = None) -> ScoringStore:
    """Configure the scoring store once and return it.

    Importing this module has no filesystem side effect.  If C does not call
    this explicitly, the first ``process`` call lazily uses ``GZZ_SCORING_DB``
    or ``server/logs/scoring/scoring.sqlite3``.
    """
    global _store
    resolved = Path(path or os.environ.get("GZZ_SCORING_DB", _DEFAULT_DB))
    with _store_lock:
        if _store is None:
            _store = ScoringStore(resolved)
        elif _store.path.resolve() != resolved.resolve():
            raise RuntimeError("scoring is already configured with a different database")
        return _store


def _get_store() -> ScoringStore:
    return _store if _store is not None else configure_scoring()


def process(
    payload: Mapping[str, Any],
    *,
    event_id: str,
    sequence: int,
) -> ProcessReceipt:
    """Adapter injected into ``server.receiver.create_router`` by C."""
    return _get_store().process_event(payload, event_id=event_id, sequence=sequence)


def get_player_snapshot(session_id: str, player_id: str) -> list[ModuleState]:
    """Return each module's latest state for a player/session."""
    return _get_store().get_player_snapshot(session_id, player_id)


def recover_from_writer(writer, *, batch_size: int = 1000) -> int:
    """Replay durable receiver records that B may have missed after a crash.

    The shared writer returns records in server storage order.  ``event_id``
    makes reprocessing safe; the recovery cursor is advanced only after each
    record has been reflected successfully.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 10000:
        raise ValueError("batch_size must be between 1 and 10000")

    store = _get_store()
    cursor = store.get_recovery_cursor()
    while True:
        batch = writer.iter_stored(after_sequence=cursor, limit=batch_size)
        if not batch:
            return cursor
        for record in batch:
            store.process_event(
                record.result,
                event_id=record.event_id,
                sequence=record.sequence,
            )
            store.advance_recovery_cursor(record.sequence)
            cursor = record.sequence
        if len(batch) < batch_size:
            return cursor
