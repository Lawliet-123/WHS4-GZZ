"""Idempotent local JSONL -> Shared enqueue handoff for module_integrity."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from shared._locking import FileLock
from shared._sqlite import database
from shared.errors import SharedError, StorageError
from shared.schema import encode_event


def _complete_local_row(path, ledger, row):
    expected = bytes(row["payload"]) + b"\n"
    offset = row["byte_offset"]
    if not path.exists():
        if offset:
            raise StorageError("local detection log is missing")
        path.touch(exist_ok=False)
    with path.open("r+b") as stream:
        size = os.fstat(stream.fileno()).st_size
        if not offset <= size <= offset + len(expected):
            raise StorageError("local detection log has an unexpected tail")
        stream.seek(offset)
        tail = stream.read(len(expected))
        if not expected.startswith(tail):
            raise StorageError("local detection log differs from pending delivery")
        stream.seek(size)
        stream.write(expected[len(tail):])
        stream.flush()
        os.fsync(stream.fileno())
    with database(ledger) as db:
        db.execute("UPDATE delivery SET local_written=1 WHERE event_id=?", (row["event_id"],))


def _recover_local(path, ledger):
    with database(ledger) as db:
        rows = db.execute("SELECT * FROM delivery WHERE local_written=0 ORDER BY rowid").fetchall()
    for row in rows:
        _complete_local_row(path, ledger, row)


def _verify_local_row(path, row):
    with path.open("rb") as stream:
        stream.seek(row["byte_offset"])
        if stream.read(len(row["payload"]) + 1) != bytes(row["payload"]) + b"\n":
            raise StorageError("local detection record differs from its delivery ledger")


def write_local_and_queue(path: Path, result, *, send):
    """Return only after durable Shared acceptance; failed handoffs survive exit.

    The sidecar tracks only records created by this writer. Existing JSONL is
    neither scanned nor replayed. Retries keep the original payload and UUID,
    including the original timestamp, without appending another local line.
    """
    path = Path(path)
    payload = encode_event(result)
    event_id = str(uuid5(NAMESPACE_URL, "gzz-module-integrity:" + sha256(payload).hexdigest()))
    ledger = path.with_name(path.name + ".shared-delivery.sqlite3")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(ledger.with_name(ledger.name + ".lock"), timeout=3):
            with database(ledger) as db:
                db.execute("""CREATE TABLE IF NOT EXISTS delivery (
                    event_id TEXT PRIMARY KEY, payload BLOB NOT NULL,
                    byte_offset INTEGER NOT NULL, local_written INTEGER NOT NULL DEFAULT 0,
                    queued INTEGER NOT NULL DEFAULT 0)""")
            _recover_local(path, ledger)
            with database(ledger) as db:
                existing = db.execute("SELECT * FROM delivery WHERE event_id=?", (event_id,)).fetchone()
                if existing is None:
                    previous = db.execute("SELECT * FROM delivery ORDER BY rowid DESC LIMIT 1").fetchone()
                    offset = path.stat().st_size if path.exists() else 0
                    if previous is not None and offset != previous["byte_offset"] + len(previous["payload"]) + 1:
                        raise StorageError("local detection log changed outside its delivery ledger")
                    if previous is not None:
                        _verify_local_row(path, previous)
                    if previous is None and offset:
                        # Only inspect the final byte of an older log. Never
                        # merge our first record into an unfinished older line.
                        with path.open("rb") as stream:
                            stream.seek(offset - 1)
                            if stream.read(1) != b"\n":
                                raise StorageError("existing local detection log has an incomplete tail")
                    db.execute("INSERT INTO delivery(event_id,payload,byte_offset) VALUES(?,?,?)",
                               (event_id, payload, offset))
                elif bytes(existing["payload"]) != payload:
                    raise StorageError("delivery ID conflicts with different content")
                else:
                    _verify_local_row(path, existing)
            _recover_local(path, ledger)
            with database(ledger) as db:
                rows = db.execute("SELECT * FROM delivery WHERE queued=0 ORDER BY rowid").fetchall()
            for row in rows:
                # Verify only the known record's bytes, never replay arbitrary
                # pre-existing local data or send a changed line.
                _verify_local_row(path, row)
                receipt = send(json.loads(bytes(row["payload"])), event_id=row["event_id"])
                # Shared's client API returns QueuedReceipt(status="queued").
                # HTTP stored/duplicate acknowledgements belong to its worker;
                # they are not valid enqueue receipts for this handoff.
                if getattr(receipt, "status", None) != "queued" or getattr(receipt, "event_id", None) != row["event_id"]:
                    raise StorageError("Shared enqueue did not return the expected durable receipt")
                with database(ledger) as db:
                    db.execute("UPDATE delivery SET queued=1 WHERE event_id=?", (row["event_id"],))
                print(f"[shared] detection {receipt.status}: {receipt.event_id}")
    except SharedError as error:
        # The runner already retains a pending batch on OSError. Do not expose
        # arbitrary exception messages, which can contain configuration values.
        raise OSError(f"Shared delivery pending: {type(error).__name__}") from None


__all__ = ["write_local_and_queue"]
