"""Portable mocked API checks; no server, game, detector or seed is executed."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse
import uuid

from server.dashboard_backend import realgame_server as runtime
from server.dashboard_backend.browser_smoke import FixtureHTTP


RUN_ID = "abcdef123456"
PRIVATE = "PRIVATE_TEST_STRING_NEVER_RETAIN"


class FakeAPI:
    def __init__(self):
        self.run_id = RUN_ID
        self.url = "http://127.0.0.1:8002"
        self.tokens = FixtureHTTP(8002, RUN_ID).tokens
        self.row = {
            "session_id": "private_session", "player_id": "private_player",
            "module": "external_access", "raw_score": 0, "timestamp_ms": 1000,
            "evidence": {"submodule": "module_integrity", "status": "NORMAL",
                         "module_path": PRIVATE, "nested": {"secret": PRIVATE}},
            "reasons": [PRIVATE], "id": str(uuid.uuid4()), "sequence": 1,
            "event_kind": "detection",
        }
        self.verdict = {
            "session_id": self.row["session_id"], "player_id": self.row["player_id"],
            "status": "INCONCLUSIVE", "assessment_complete": False,
            "evidence_unit_count": 0, "active_modules": [],
            "unresolved_modules": ["external_access"], "deferred_modules": [],
            "unavailable_modules": [], "reason_codes": [PRIVATE],
        }
        self.source = {
            "client_id": "launcher-1000", "role": "launcher", "sequence": 1,
            "state": "healthy", "components": [
                {"id": "launcher", "state": "running", "required": True, "pid": 123,
                 "details": {"private": PRIVATE}},
            ],
        }
        self.empty = False
        self.detail_changed = False
        self.verdict_changed = False
        self.extra_count = 0
        self.heartbeat_changed = False
        self.calls = []

    def request(self, path):
        self.calls.append(path)
        route = urlparse(path).path
        if route == "/api/dashboard/overview":
            return {"schema_version": "dashboard-v0", "counts": {"events": int(not self.empty) + self.extra_count},
                    "assessments": [] if self.empty else [{"session_id": self.row["session_id"],
                                                           "player_id": self.row["player_id"]}],
                    "session_page": {"has_more": False}, "index": {"catching_up": False},
                    "connection": {"Receiver": {"state": "online"}, "Scoring": {"state": "online"},
                                   "Launcher": {"state": "unknown" if self.empty else "online"}}}
        if route == "/api/dashboard/events":
            return {"items": [] if self.empty else [deepcopy(self.row)],
                    "has_more": False, "through_sequence": int(not self.empty)}
        if route.startswith("/api/dashboard/events/"):
            row = deepcopy(self.row)
            if self.detail_changed:
                row["evidence"]["status"] = "ERROR"
            return row
        if route.endswith("/snapshot"):
            return {"session_id": self.row["session_id"], "player_id": self.row["player_id"],
                    "modules": [deepcopy(self.row)], "status": self.verdict["status"],
                    "final_verdict": deepcopy(self.verdict), "score": None, "confidence": None}
        if route.startswith("/api/dashboard/verdict/"):
            verdict = deepcopy(self.verdict)
            if self.verdict_changed:
                verdict["assessment_complete"] = True
            return verdict
        if route.endswith("/status"):
            return {"session_id": self.row["session_id"], "player_id": self.row["player_id"],
                    "sources": [deepcopy(self.source)], "launcher": {"state": "healthy"},
                    "has_more_sources": False}
        if route.startswith("/api/dashboard/heartbeat/"):
            return {"session_id": self.row["session_id"], "client_id": self.source["client_id"],
                    "sequence": 2 if self.heartbeat_changed else 1,
                    "payload": {"player_id": self.row["player_id"], "private": PRIVATE}}
        raise AssertionError("unexpected API category")


class RealgameRuntimeTests(unittest.TestCase):
    def test_summary_retains_only_allowlisted_counts_and_states(self):
        api = FakeAPI()
        summary = runtime.collect_summary(api)
        self.assertEqual(summary["result"], "OBSERVED")
        self.assertEqual(summary["api_consistency"], "VERIFIED")
        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["event_detail_checked_count"], 1)
        self.assertEqual(summary["subjects"][0]["verdict_status"], "INCONCLUSIVE")
        self.assertFalse(summary["subjects"][0]["assessment_complete"])
        serialized = json.dumps(summary)
        for excluded in (PRIVATE, "private_session", "private_player", "launcher-1000", api.row["id"]):
            self.assertNotIn(excluded, serialized)
        self.assertNotIn("PASS", serialized)
        self.assertTrue(all(path.startswith("/api/dashboard/") for path in api.calls))

    def test_empty_start_is_never_reported_as_verified_realgame(self):
        api = FakeAPI()
        api.empty = True
        summary = runtime.collect_summary(api)
        self.assertEqual(summary["result"], "NO_OBSERVATIONS")
        self.assertEqual(summary["api_consistency"], "INCOMPLETE")
        self.assertFalse(summary["checks"]["launcher_heartbeat_observed"])
        self.assertEqual(summary["subjects"], [])

    def test_mismatched_api_projections_fail_consistency(self):
        for attribute, issue in (
            ("detail_changed", "event_list_detail_mismatch"),
            ("verdict_changed", "snapshot_verdict_mismatch"),
            ("heartbeat_changed", "heartbeat_status_sequence_changed_or_mismatched"),
            ("extra_count", "event_count_changed_or_incomplete"),
        ):
            api = FakeAPI()
            setattr(api, attribute, 1)
            with self.subTest(attribute=attribute):
                summary = runtime.collect_summary(api)
                self.assertEqual(summary["api_consistency"], "INCOMPLETE")
                self.assertIn(issue, summary["issues"])

    def test_unknown_names_and_states_are_not_echoed(self):
        api = FakeAPI()
        api.row["module"] = PRIVATE
        api.row["evidence"]["submodule"] = PRIVATE
        api.row["evidence"]["status"] = PRIVATE
        api.source["components"][0]["id"] = PRIVATE
        api.source["components"][0]["state"] = PRIVATE
        summary = runtime.collect_summary(api)
        self.assertNotIn(PRIVATE, json.dumps(summary))
        self.assertEqual(summary["module_event_counts"], {"other": 1})

    def test_broken_or_unbounded_pagination_is_incomplete(self):
        api = Mock()
        api.run_id = RUN_ID
        api.request.return_value = {"items": [], "has_more": True, "next_cursor": "same",
                                    "through_sequence": 0}
        observation = runtime.APIObservation(api)
        _, _, complete = observation.events()
        self.assertFalse(complete)
        self.assertIn("invalid_events_cursor", observation.issues)
        self.assertEqual(api.request.call_count, 2)
        self.assertEqual(parse_qs(urlparse(api.request.call_args.args[0]).query)["cursor"], ["same"])

    def test_invalid_bounds_and_nonempty_output_refused_before_ownership(self):
        with tempfile.TemporaryDirectory(prefix="realgame-runtime-test-") as folder:
            with patch.object(runtime, "owned_server") as owned:
                for duration in (True, 0, 29, 3601):
                    with self.subTest(duration=duration), self.assertRaises(ValueError):
                        runtime.serve(8002, duration, folder)
                for port in (True, 0, 65536):
                    with self.subTest(port=port), self.assertRaises(ValueError):
                        runtime.serve(port, 30, folder)
                marker = Path(folder) / "existing.json"
                marker.write_text("original", encoding="utf-8")
                with self.assertRaises(ValueError):
                    runtime.serve(8002, 30, folder)
                self.assertEqual(marker.read_text(encoding="utf-8"), "original")
                owned.assert_not_called()

    def test_summary_is_captured_before_owned_cleanup_and_stop_marker_works(self):
        api = FakeAPI()
        phases = []
        with tempfile.TemporaryDirectory(prefix="realgame-runtime-test-") as folder:
            @contextmanager
            def owned(port, run_id):
                phases.append("owned")
                yield api, Path(folder) / "unretained-private-store"
                self.assertTrue((Path(folder) / "summary.json").exists())
                phases.append("cleanup")

            def capture(http):
                phases.append("capture")
                return {"api_consistency": "VERIFIED", "payloads_retained": False}

            # Request stop only after setup is written in the validated fresh directory.
            original_write = runtime.write_json
            def write(path, value):
                original_write(path, value)
                if path.name == "setup.json":
                    (Path(folder) / "stop.request").touch()

            output = io.StringIO()
            with patch.object(runtime, "owned_server", owned), \
                    patch.object(runtime, "collect_summary", capture), \
                    patch.object(runtime, "write_json", write), redirect_stdout(output):
                summary = runtime.serve(8002, 30, folder)
            self.assertEqual(phases, ["owned", "capture", "cleanup"])
            self.assertEqual(summary["stop_reason"], "stop_requested")
            self.assertTrue(summary["owned_listener_stopped"])
            setup = json.loads((Path(folder) / "setup.json").read_text(encoding="utf-8"))
            self.assertEqual(setup["test_tokens"], api.tokens)
            self.assertEqual(setup["seeded_events"], 0)

    def test_cli_redacts_exception_contents(self):
        stderr = io.StringIO()
        with patch.object(runtime.sys, "argv", ["realgame_server", "serve", "--output", "unused"]), \
                patch.object(runtime, "serve", side_effect=RuntimeError(PRIVATE)), redirect_stderr(stderr):
            self.assertEqual(runtime.main(), 1)
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn(PRIVATE, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
