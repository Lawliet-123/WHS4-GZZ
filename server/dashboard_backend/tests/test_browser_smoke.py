"""Offline fixture safety/real-B contract checks; no child server or game runs."""

import json
import socket
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import server.scoring.main as scoring
from server.dashboard_backend import DashboardService
from server.dashboard_backend import browser_smoke as fixture
from server.receiver.heartbeat_schema import decode_heartbeat
from server.receiver.heartbeat_store import HeartbeatStore
from shared.config import WriterConfig
from shared.schema import encode_event
from shared.storage import DetectionWriter


RUN_ID = "abcdef123456"


class BrowserFixtureTests(unittest.TestCase):
    def test_child_environment_discards_live_overrides_and_uses_temp_stores(self):
        source = {"SystemRoot": "synthetic-system-root", "PATH": "synthetic-path",
                  "GZZ_TELEMETRY_LOG_ROOT": "must-not-use", "GZZ_SCORING_DB": "must-not-use",
                  "GZZ_DASHBOARD_TOKEN": "must-not-use", "MECCHA_HEARTBEAT_DB": "must-not-use",
                  "GZZ_DASHBOARD_CURSOR_SECRET": "must-not-use", "HTTPS_PROXY": "must-not-use",
                  "PYTHONPATH": "must-not-use", "UNRELATED_CREDENTIAL": "must-not-use"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = fixture.child_environment(root, RUN_ID, source)
            self.assertEqual(env["GZZ_DASHBOARD_TOKEN"], fixture.test_tokens(RUN_ID)["GZZ_DASHBOARD_TOKEN"])
            for name in ("GZZ_TELEMETRY_LOG_ROOT", "GZZ_SCORING_DB", "MECCHA_HEARTBEAT_DB",
                         "GZZ_DASHBOARD_INDEX", "TEMP", "TMP"):
                self.assertTrue(Path(env[name]).resolve().is_relative_to(root.resolve()))
            self.assertNotIn("must-not-use", env.values())
            self.assertNotIn("GZZ_DASHBOARD_CURSOR_SECRET", env)
            self.assertNotIn("PYTHONPATH", env)
            self.assertEqual(source["GZZ_SCORING_DB"], "must-not-use")

    def test_occupied_port_fails_before_child_temp_store_or_post(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with patch.object(fixture.subprocess, "Popen") as popen, \
                    patch.object(fixture.tempfile, "TemporaryDirectory") as temporary, \
                    patch.object(fixture.FixtureHTTP, "request") as request:
                with self.assertRaises(RuntimeError):
                    fixture.serve(listener.getsockname()[1], 30)
                popen.assert_not_called()
                temporary.assert_not_called()
                request.assert_not_called()

    def test_loopback_and_parameter_boundaries(self):
        http = fixture.FixtureHTTP(8002, RUN_ID)
        self.assertEqual(http.url, "http://127.0.0.1:8002")
        for port in (True, 0, 1023, 65536, "8002"):
            with self.assertRaises(ValueError):
                fixture.FixtureHTTP(port, RUN_ID)
        for value in ("", "ABCDEF123456", "../abcdef123456", "abcdef123456?other"):
            with self.assertRaises(ValueError):
                fixture.FixtureHTTP(8002, value)
        with patch.object(http.opener, "open") as open_request:
            for path in ("https://example.invalid/api", "//example.invalid/api", "/api/\\other"):
                with self.assertRaises(ValueError):
                    http.request(path)
            open_request.assert_not_called()
        with self.assertRaises(ValueError):
            fixture.serve(8002, 3601)
        self.assertIsNone(fixture._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid"))

    def test_add_refuses_unrelated_or_incomplete_fixture_before_mutation(self):
        for overview in ({}, {"schema_version": "dashboard-v0", "assessments": []},
                         {"schema_version": "dashboard-v0", "assessments": [
                             {"session_id": fixture.session_id(RUN_ID, case), "player_id": fixture.PLAYER}
                             for case in fixture.CASES], "session_page": {"has_more": True}}):
            with patch.object(fixture.FixtureHTTP, "request", return_value=overview), \
                    patch.object(fixture, "deliver") as deliver, \
                    patch.object(fixture, "refresh_heartbeat") as refresh:
                with self.assertRaises(RuntimeError):
                    fixture.add(8002, RUN_ID)
                deliver.assert_not_called()
                refresh.assert_not_called()

    def test_all_payloads_validate_and_real_B_preserves_four_assessments(self):
        # No fake verdict provider: use current B queries against an explicit temp DB.
        with tempfile.TemporaryDirectory() as folder, patch.object(scoring, "_store", None):
            root = Path(folder)
            scoring.configure_scoring(root / "scoring.sqlite3")
            writer = DetectionWriter(WriterConfig(root=root / "detections"))
            heartbeats = HeartbeatStore(root / "heartbeat.sqlite3")
            for event in fixture.seed_events(RUN_ID):
                encode_event(event)
                event_id = str(uuid.uuid4())
                receipt = writer.write_detection(event, event_id=event_id)
                scoring.process(event, event_id=event_id, sequence=receipt.sequence)
            for case in fixture.CASES:
                payload = fixture.heartbeat(RUN_ID, case, 1)
                validated = decode_heartbeat(json.dumps(payload).encode())
                self.assertTrue(all(item["pid"] is None for item in validated["components"].values()))
                heartbeats.accept(validated)
            service = DashboardService(writer=writer, scoring=scoring, heartbeat_store=heartbeats,
                                       verdict_provider=scoring.get_player_final_verdict,
                                       index_path=root / "dashboard.sqlite3", cursor_secret="synthetic-cursor")
            overview = service.overview()
            self.assertEqual(len(overview["assessments"]), 4)
            self.assertEqual(overview["counts"]["events"], 6)
            self.assertEqual(overview["connection"]["Launcher"]["connected_pairs"], 4)
            for case, expected in fixture.CASES.items():
                row = next(row for row in overview["assessments"]
                           if row["session_id"] == fixture.session_id(RUN_ID, case))
                self.assertEqual(row["status"], expected)
                self.assertIsNone(row["score"])
                self.assertIsNone(row["confidence"])
                if case == "unknown":
                    self.assertFalse(row["assessment_available"])
                    self.assertIsNone(row["final_verdict"])
                    self.assertEqual(row["data_state"], "missing")
                else:
                    self.assertTrue(row["assessment_available"])
                    self.assertEqual(row["final_verdict"]["status"], expected)
                    self.assertIn("assessment_complete", row["final_verdict"])
                    self.assertIn("overlap_adjustment_count", row["final_verdict"])

    def test_owned_child_cleanup_escalates_only_after_timeout(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("synthetic-child", 5), 0]
        fixture.terminate_child(process)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)
        process = Mock()
        process.poll.return_value = 1
        fixture.terminate_child(process)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
