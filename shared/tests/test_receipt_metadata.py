"""Acceptance clock metadata never changes the seven-field Event or retry identity."""
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from shared.config import WriterConfig
from shared.errors import StorageError
from shared.schema import EVENT_FIELDS
from shared.storage import DetectionWriter, StoredDetection


def event():
    return {"session_id": "session1", "player_id": "player1", "module": "esp",
            "timestamp_ms": 84000, "evidence": {}, "reasons": [], "raw_score": 0}


class ReceiptMetadataTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = WriterConfig(root=self.root)
        self.first = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)

    def test_first_acceptance_preserves_jsonl_and_ack_contract(self):
        writer = DetectionWriter(self.config)
        key = str(uuid.uuid4())
        with patch("shared.storage.datetime") as clock:
            clock.now.return_value = self.first
            receipt = writer.write_detection(event(), event_id=key)
            clock.now.assert_called_once_with(timezone.utc)
        record = writer.iter_stored()[0]
        self.assertEqual(record.received_at_utc, self.first.isoformat())
        self.assertEqual(record.result, event())
        self.assertEqual(set(json.loads(receipt.path.read_text().strip())), EVENT_FIELDS)
        self.assertEqual(receipt.to_ack(), {"event_id": key, "status": "stored"})

    def test_duplicate_and_restart_never_refresh_first_receipt(self):
        writer = DetectionWriter(self.config)
        key = str(uuid.uuid4())
        with patch("shared.storage.datetime") as clock:
            clock.now.return_value = self.first
            writer.write_detection(event(), event_id=key)
        restarted = DetectionWriter(self.config)
        with patch("shared.storage.datetime") as clock:
            receipt = restarted.write_detection(event(), event_id=key)
            clock.now.assert_not_called()
        self.assertEqual(receipt.to_ack(), {"event_id": key, "status": "duplicate"})
        self.assertEqual(restarted.iter_stored()[0].received_at_utc, self.first.isoformat())

    def test_pending_recovery_keeps_original_acceptance_clock(self):
        writer = DetectionWriter(self.config)
        with patch("shared.storage.datetime") as clock, patch.object(writer, "_append_pending", side_effect=OSError("test interrupted append")):
            clock.now.return_value = self.first
            with self.assertRaises(StorageError):
                writer.write_detection(event(), event_id=str(uuid.uuid4()))
        with patch("shared.storage.datetime") as clock:
            restarted = DetectionWriter(self.config)
            record = restarted.iter_stored()[0]
            clock.now.assert_not_called()
        self.assertEqual(record.received_at_utc, self.first.isoformat())
        self.assertEqual(record.result, event())

    def test_legacy_writer_migration_is_idempotent_without_time_backfill(self):
        # Recreate the previous schema, not a persisted operational database.
        writer = DetectionWriter(self.config)
        key = str(uuid.uuid4())
        writer.write_detection(event(), event_id=key)
        with closing(sqlite3.connect(self.root / "writer-index.sqlite3")) as db, db:
            db.execute("ALTER TABLE stored RENAME TO legacy_stored")
            db.execute("""CREATE TABLE stored (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                digest TEXT NOT NULL, payload BLOB NOT NULL, relative_path TEXT NOT NULL,
                byte_offset INTEGER NOT NULL, state TEXT NOT NULL)""")
            db.execute("INSERT INTO stored SELECT sequence,event_id,digest,payload,relative_path,byte_offset,state FROM legacy_stored")
            db.execute("DROP TABLE legacy_stored")
        for _ in range(2):
            migrated = DetectionWriter(self.config)
            self.assertIsNone(migrated.iter_stored()[0].received_at_utc)
            self.assertEqual(migrated.write_detection(event(), event_id=key).status, "duplicate")
        self.assertIsNone(StoredDetection(1, key, event()).received_at_utc)
        migrated.write_detection({**event(), "timestamp_ms": 85000}, event_id=str(uuid.uuid4()))
        self.assertIsNotNone(migrated.iter_stored()[1].received_at_utc)


if __name__ == "__main__":
    unittest.main()
