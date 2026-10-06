"""Additive Dashboard clock metadata with durable first acceptance and no guessed clocks."""
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.dashboard_backend import DashboardService, create_dashboard_router
from server.dashboard_backend.index import DashboardIndex
from server.receiver.router import bearer_token_verifier, create_router
from server.scoring.storage import ScoringStore
from shared.config import WriterConfig
from shared.storage import DetectionWriter, StoredDetection


def event(evidence=None):
    return {"session_id": "session1", "player_id": "player1", "module": "esp",
            "timestamp_ms": 84000, "evidence": evidence or {}, "reasons": [], "raw_score": 0}


class DashboardEventTimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def make_index(self, records):
        writer = SimpleNamespace(root=self.root,
            iter_stored=lambda after_sequence=0, limit=500: [record for record in records if record.sequence > after_sequence][:limit])
        return DashboardIndex(self.root / "dashboard.sqlite3", writer)

    def test_declared_basis_and_real_receipt_survive_list_and_detail(self):
        key = str(uuid.uuid4())
        index = self.make_index([StoredDetection(1, key, event({"time_basis": "session_relative"}), "2026-10-06T15:00:00Z")])
        index.sync()
        detail = index.detail(key)
        self.assertEqual(detail["received_at_utc"], "2026-10-06T15:00:00Z")
        self.assertEqual(detail["time_basis"], "session_relative")
        self.assertEqual(index.events({}, after=0, through=1, limit=100)[0], [detail])
        self.assertNotIn("observed_at_utc", detail)

    def test_missing_receipt_and_unrecognized_basis_remain_unknown(self):
        records = [StoredDetection(index + 1, str(uuid.uuid4()), event({"time_basis": basis}))
                   for index, basis in enumerate([None, "UTC", "detector_uptime", 123, {}, []])]
        index = self.make_index(records)
        index.sync()
        for detail in index.events({}, after=0, through=len(records), limit=100)[0]:
            self.assertIsNone(detail["received_at_utc"])
            self.assertEqual(detail["time_basis"], "unknown")

    def test_epoch_requires_explicit_basis_and_invalid_receipt_is_not_copied(self):
        key = str(uuid.uuid4())
        index = self.make_index([StoredDetection(1, key, event({"time_basis": "unix_epoch_ms"}), "2026-02-30T15:00:00Z")])
        index.sync()
        self.assertIsNone(index.detail(key)["received_at_utc"])
        self.assertEqual(index.detail(key)["time_basis"], "unix_epoch_ms")

    def test_old_dashboard_index_migrates_without_backfilling_existing_events(self):
        key = str(uuid.uuid4())
        path = self.root / "dashboard.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO metadata VALUES ('cursor','1');
                CREATE TABLE events (sequence INTEGER PRIMARY KEY,id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL,player_id TEXT NOT NULL,module TEXT NOT NULL,submodule TEXT,
                timestamp_ms INTEGER NOT NULL,payload TEXT NOT NULL,kind TEXT NOT NULL);""")
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)", (1, key, "session1", "player1", "esp", None, 84000, json.dumps(event()), "detection"))
        for _ in range(2):
            index = self.make_index([])
            index.sync()
            self.assertIsNone(index.detail(key)["received_at_utc"])

    def test_receiver_duplicate_retains_first_receipt_and_shared_seven_fields(self):
        writer = DetectionWriter(WriterConfig(root=self.root / "detections"))
        scoring = ScoringStore(self.root / "scoring.sqlite3")
        service = DashboardService(writer=writer, scoring=scoring, index_path=self.root / "dashboard.sqlite3", cursor_secret="test-cursor")
        app = FastAPI()
        app.include_router(create_router(writer.write_detection, scoring.process_event, verify_token=bearer_token_verifier("test-detection")))
        app.include_router(create_dashboard_router(service, verify_token=bearer_token_verifier("test-dashboard")))
        key = str(uuid.uuid4())
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer test-detection", "Idempotency-Key": key, "X-GZZ-Protocol-Version": "1"}
            with patch("shared.storage.datetime") as clock:
                clock.now.return_value = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
                response = client.post("/api/detection", json=event({"time_basis": "session_relative"}), headers=headers)
            self.assertEqual(response.json(), {"event_id": key, "status": "stored"})
            self.assertEqual(client.post("/api/detection", json=event({"time_basis": "session_relative"}), headers=headers).json()["status"], "duplicate")
            dashboard_headers = {"Authorization": "Bearer test-dashboard"}
            listed = client.get("/api/dashboard/events", headers=dashboard_headers).json()["items"][0]
            detail = client.get(f"/api/dashboard/events/{key}", headers=dashboard_headers).json()
        self.assertEqual(listed, detail)
        self.assertEqual(detail["received_at_utc"], "2026-10-06T15:00:00+00:00")
        self.assertEqual(detail["time_basis"], "session_relative")
        self.assertEqual(set(writer.iter_stored()[0].result), set(event()))


if __name__ == "__main__":
    unittest.main()
