"""Synthetic, in-process C API contracts; not real-game or network E2E.

Only fresh temporary stores and disposable authentication literals are used.
The actual server.main app runs through TestClient without sockets/subprocesses.
"""
from contextlib import contextmanager
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from uuid import uuid4

from fastapi.testclient import TestClient

import server
from server.dashboard_backend import calibration_export as export
from server.scoring import main as scoring_queries
from shared import logger
from shared.schema import EVENT_FIELDS


RUN_ID = "abcdef123456"
SESSION = "synthetic_esp_contract"
PLAYER = "synthetic_contract_player"
_MISSING = object()


class _InProcessHTTP:
    """Exporter adapter: every request goes to TestClient, never this URL."""

    url = "http://127.0.0.1:8002"
    run_id = RUN_ID

    def __init__(self, client, token):
        self.client = client
        self.token = token
        self.responses = []

    def request(self, path):
        response = self.client.get(path, headers={"Authorization": "Bearer " + self.token})
        self.responses.append((path, response.status_code))
        if response.status_code != 200:
            # Match FixtureHTTP's error contract without retaining response bodies.
            raise urllib.error.HTTPError(self.url + path, response.status_code,
                                         "synthetic contract status", {}, None)
        return response.json()


@contextmanager
def _isolated_c_app():
    with tempfile.TemporaryDirectory(prefix="calibration-server-contract-") as directory:
        root = Path(directory)
        environment = {
            "GZZ_TELEMETRY_TOKEN": "synthetic-browser-receiver-" + RUN_ID,
            "MECCHA_HEARTBEAT_TOKEN": "synthetic-browser-heartbeat-" + RUN_ID,
            "GZZ_DASHBOARD_TOKEN": "synthetic-browser-dashboard-" + RUN_ID,
            "GZZ_TELEMETRY_LOG_ROOT": str(root / "detections"),
            "GZZ_SCORING_DB": str(root / "scoring.sqlite3"),
            "MECCHA_HEARTBEAT_DB": str(root / "heartbeat.sqlite3"),
            "GZZ_DASHBOARD_INDEX": str(root / "dashboard.sqlite3"),
            "GZZ_DASHBOARD_STALE_AFTER_MS": "30000",
            "TEMP": str(root), "TMP": str(root),
        }
        # Import C anew under controlled settings; restore both import caches and
        # its parent package attribute, plus Shared/Scoring facades, on every exit.
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(logger, "_writer", None), \
                patch.object(scoring_queries, "_store", None), \
                patch.dict(sys.modules), patch.object(server, "main", None, create=True):
            sys.modules.pop("server.main", None)
            central = importlib.import_module("server.main")
            with TestClient(central.app) as client:
                yield client, _InProcessHTTP(client, environment["GZZ_DASHBOARD_TOKEN"]), environment


class CalibrationServerContractTests(unittest.TestCase):
    def existing_globals(self):
        return (logger._writer, scoring_queries._store,
                sys.modules.get("server.main", _MISSING), getattr(server, "main", _MISSING))

    def assert_globals_restored(self, previous):
        for actual, original in zip(self.existing_globals(), previous):
            self.assertIs(actual, original)

    def test_current_c_clock_projection_preserves_original_events_and_scoring(self):
        cases = (
            ({"timestamp_basis": "launcher_session_start", "session_start_unix_ms": 1791367718186},
             "session_relative", "2026-10-07T10:12:44.957+00:00"),
            ({"time_basis": "session_relative", "timestamp_basis": "launcher_session_start",
              "session_start_unix_ms": 1791367718186}, "session_relative", "2026-10-07T10:12:44.957+00:00"),
            ({"time_basis": "session_relative"}, "session_relative", None),
            ({"time_basis": "session_relative", "session_start_unix_ms": True}, "session_relative", None),
            ({"time_basis": "unix_epoch_ms"}, "unix_epoch_ms", None),
            ({"time_basis": "unix_epoch_ms", "timestamp_basis": "launcher_session_start",
              "session_start_unix_ms": 1791367718186}, "unknown", None),
            ({"timestamp_basis": "invalid"}, "unknown", None),
            ({}, "unknown", None),
        )
        previous = self.existing_globals()
        for clock, basis, observed in cases:
            with self.subTest(clock=clock), _isolated_c_app() as (client, http, environment):
                event_id = str(uuid4())
                event = {"session_id": SESSION, "player_id": PLAYER, "module": "esp",
                         "timestamp_ms": 246771, "raw_score": 3, "evidence": clock,
                         "reasons": ["Synthetic current-clock contract; no game observation"]}
                headers = {"Authorization": "Bearer " + environment["GZZ_TELEMETRY_TOKEN"],
                           "Idempotency-Key": event_id, "X-GZZ-Protocol-Version": "1"}
                response = client.post("/api/detection", json=event, headers=headers)
                self.assertEqual(response.status_code, 200)
                first = export.collect_evidence(http, SESSION, PLAYER, expected_events={event_id: event})
                row = first["events"][0]
                self.assertEqual(row["time_basis"], basis)
                self.assertEqual(row.get("observed_at_utc"), observed)
                self.assertEqual({key: row[key] for key in EVENT_FIELDS}, event)
                self.assertTrue(first["checks"]["declared_clock_metadata_equal"])
                self.assertEqual(first["snapshot"]["final_verdict"], first["final_verdict"])
                duplicate = client.post("/api/detection", json=event, headers=headers)
                self.assertEqual(duplicate.json(), {"event_id": event_id, "status": "duplicate"})
                second = export.collect_evidence(http, SESSION, PLAYER, expected_events={event_id: event})
                self.assertEqual(second["events"], first["events"])
            self.assert_globals_restored(previous)

    def test_empty_normal_capture_keeps_unknown_and_c_verdict_404(self):
        previous = self.existing_globals()
        with _isolated_c_app() as (client, http, _):
            document = export.collect_evidence(http, SESSION, PLAYER, expected_events={})
            self.assertEqual(document["result"], "SERVER_READBACK_VERIFIED")
            self.assertEqual(document["event_count"], 0)
            self.assertEqual(document["events"], [])
            self.assertEqual(document["snapshot"]["modules"], [])
            self.assertEqual(document["snapshot"]["status"], "UNKNOWN")
            self.assertFalse(document["snapshot"]["assessment_available"])
            self.assertEqual(document["snapshot"]["data_state"], "missing")
            self.assertIsNone(document["snapshot"]["score"])
            self.assertIsNone(document["snapshot"]["confidence"])
            self.assertIsNone(document["snapshot"]["final_verdict"])
            self.assertIsNone(document["final_verdict"])
            self.assertEqual(http.responses[-1],
                             (f"/api/dashboard/verdict/{SESSION}/{PLAYER}", 404))
        self.assert_globals_restored(previous)

    def test_two_synthetic_esp_events_match_actual_rows_snapshot_and_verdict(self):
        previous = self.existing_globals()
        with _isolated_c_app() as (client, http, environment):
            expected = {}
            # Reverse timestamps prove sequence ordering is not observation-time
            # ordering, and the late zero does not replace the newer snapshot.
            for timestamp, raw in ((2000, 3), (1000, 0)):
                event_id = str(uuid4())
                event = {
                    "session_id": SESSION, "player_id": PLAYER, "module": "esp",
                    "timestamp_ms": timestamp, "raw_score": raw,
                    "evidence": {
                        "status": "SUSPICIOUS" if raw else "NORMAL",
                        "time_basis": "session_relative", "source_image": "python.exe",
                        "source_image_path_sha256": "a" * 64,
                        "sensor_event_id": "sensor-sha256:" + "b" * 64,
                        "event_type": "process_access", "categories": ["process_tamper"],
                    },
                    "reasons": ["Synthetic contract input; no game observation"],
                }
                response = client.post("/api/detection", json=event, headers={
                    "Authorization": "Bearer " + environment["GZZ_TELEMETRY_TOKEN"],
                    "Idempotency-Key": event_id, "X-GZZ-Protocol-Version": "1",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"event_id": event_id, "status": "stored"})
                expected[event_id] = event

            document = export.collect_evidence(http, SESSION, PLAYER, expected_events=expected)
            rows, snapshot = document["events"], document["snapshot"]
            self.assertEqual(document["event_count"], 2)
            self.assertEqual([row["sequence"] for row in rows], [1, 2])
            self.assertEqual([row["timestamp_ms"] for row in rows], [2000, 1000])
            self.assertEqual([row["event_kind"] for row in rows], ["detection", "detection"])
            self.assertEqual([row["time_basis"] for row in rows],
                             ["session_relative", "session_relative"])
            for row in rows:
                self.assertEqual(set(row), export._ROW_FIELDS - {"observed_at_utc"})
                self.assertIsNotNone(row["received_at_utc"])
                self.assertEqual({key: row[key] for key in EVENT_FIELDS}, expected[row["id"]])
            self.assertTrue(document["checks"]["list_detail_equal"])
            self.assertTrue(set(snapshot).issubset(export._SNAPSHOT_FIELDS))
            self.assertEqual(set(snapshot["modules"][0]), EVENT_FIELDS | {"event_id", "sequence"})
            self.assertEqual(snapshot["modules"][0]["event_id"], rows[0]["id"])
            self.assertEqual(snapshot["modules"][0]["raw_score"], 3)
            self.assertTrue({"module_evidence", "aggregate_evidence", "aggregate_risk"}
                            .issubset(snapshot["policy"]))
            self.assertTrue(snapshot["assessment_available"])
            self.assertEqual(snapshot["final_verdict"], document["final_verdict"])
            self.assertEqual(snapshot["status"], document["final_verdict"]["status"])
            self.assertTrue(set(document["final_verdict"]).issubset(export._VERDICT_FIELDS))
            self.assertIsNone(snapshot["score"])
            self.assertIsNone(snapshot["confidence"])
            self.assertFalse(document["snapshot_watermark_atomic"])
            self.assertEqual(document["capture_provenance"], "NOT_ASSESSED")
        self.assert_globals_restored(previous)


if __name__ == "__main__":
    unittest.main()
