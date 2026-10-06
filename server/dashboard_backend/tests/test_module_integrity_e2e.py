"""Owned server.main fixture safety checks and one native Windows integration.

Portable tests use explicit mocks; the Windows case creates only owned helpers,
loopback server, temporary stores and outbox. No existing artifacts are read.
"""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from server.dashboard_backend import module_integrity_e2e as fixture


RUN_ID = "abcdef123456"


class NativeFixtureSafetyTests(unittest.TestCase):
    def test_occupied_port_refused_before_stores_children_or_sender(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with patch.object(fixture, "require_native_platform"), \
                    patch.object(fixture.tempfile, "TemporaryDirectory") as temporary, \
                    patch.object(fixture.subprocess, "Popen") as child, \
                    patch.object(fixture, "configure_client") as sender:
                with self.assertRaises(RuntimeError):
                    fixture.run_check(listener.getsockname()[1])
                temporary.assert_not_called()
                child.assert_not_called()
                sender.assert_not_called()

    def test_invalid_bounds_or_platform_refused_before_child(self):
        with patch.object(fixture.subprocess, "Popen") as child, \
                patch.object(fixture.tempfile, "TemporaryDirectory") as temporary:
            for duration in (True, -1, 1, 29, 3601, "300"):
                with self.subTest(duration=duration), self.assertRaises(ValueError):
                    fixture.run_check(duration=duration)
            for port in (True, 0, 1023, 65536, "8002"):
                with self.subTest(port=port), self.assertRaises(ValueError):
                    with fixture.owned_server(port, RUN_ID):
                        self.fail("invalid port must not yield a server")
            with patch.object(fixture.struct, "calcsize", return_value=4):
                with self.assertRaises(RuntimeError):
                    fixture.run_check()
            child.assert_not_called()
            temporary.assert_not_called()

    def test_owned_server_refuses_unrelated_data_and_cleans_only_its_child(self):
        process = Mock()
        process.poll.return_value = None
        response = {"schema_version": "dashboard-v0", "assessments": [], "counts": {"events": 1}}
        with patch.object(fixture, "require_free_port"), \
                patch.object(fixture.subprocess, "Popen", return_value=process) as launch, \
                patch.object(fixture.FixtureHTTP, "request", return_value=response) as request:
            with self.assertRaises(RuntimeError):
                with fixture.owned_server(8002, RUN_ID):
                    self.fail("nonempty service must not receive lab data")
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args, ("/api/dashboard/overview",))
            process.terminate.assert_called_once_with()
            process.kill.assert_not_called()
            env = launch.call_args.kwargs["env"]
            self.assertEqual(env["GZZ_DASHBOARD_TOKEN"], "synthetic-browser-dashboard-" + RUN_ID)
            self.assertNotIn("PYTHONPATH", env)
            self.assertNotIn("HTTPS_PROXY", env)
            store = Path(env["GZZ_SCORING_DB"])
            self.assertFalse(store.parent.exists(), "owned temporary root must be removed")

    def test_native_failure_stops_owned_shared_sender(self):
        with tempfile.TemporaryDirectory() as directory:
            http = fixture.FixtureHTTP(8002, RUN_ID)

            @contextmanager
            def server(port=None):
                yield http, Path(directory)

            with patch.object(fixture, "require_native_platform"), \
                    patch.object(fixture, "owned_server", server), \
                    patch.object(fixture, "configure_client") as configure, \
                    patch.object(fixture, "native_observation", side_effect=RuntimeError("synthetic failure")), \
                    patch.object(fixture, "shutdown_client", return_value=True) as shutdown:
                with self.assertRaises(RuntimeError):
                    fixture.run_check()
                config = configure.call_args.args[0]
                self.assertEqual(config.server_url, "http://127.0.0.1:8002")
                self.assertFalse(config.use_environment_proxy)
                self.assertEqual(config.outbox_path, Path(directory) / "outbox.sqlite3")
                shutdown.assert_called_once_with(timeout=5)

    def test_api_payload_or_ack_mismatch_fails_verification(self):
        original = {"session_id": fixture.session_id(RUN_ID), "player_id": fixture.PLAYER,
                    "module": "external_access", "timestamp_ms": 1000,
                    "evidence": {"submodule": "module_integrity", "status": "NORMAL"},
                    "raw_score": 0, "reasons": []}
        row = {**deepcopy(original), "id": str(uuid.uuid4()), "sequence": 1}
        http = Mock()
        http.request.side_effect = [{"items": [row], "has_more": False}, row]
        with patch.object(fixture, "get_client_status", return_value=SimpleNamespace(acknowledged_this_run=1)):
            self.assertEqual(fixture.verify_events(http, [original]), [row])
        corrupted = deepcopy(row)
        corrupted["evidence"]["submodule"] = "external_process"
        http.request.side_effect = [{"items": [corrupted], "has_more": False}]
        with self.assertRaises(RuntimeError):
            fixture.verify_events(http, [original])
        http.request.side_effect = [{"items": [row], "has_more": False}, row]
        with patch.object(fixture, "get_client_status", return_value=SimpleNamespace(acknowledged_this_run=0)):
            with self.assertRaises(RuntimeError):
                fixture.verify_events(http, [original])

    def test_cli_failure_redacts_exception_contents(self):
        error = io.StringIO()
        with patch.object(fixture.sys, "argv", ["module_integrity_e2e"]), \
                patch.object(fixture, "run_check", side_effect=RuntimeError("must-not-print-test-secret")), \
                redirect_stderr(error):
            self.assertEqual(fixture.main(), 1)
        self.assertIn("RuntimeError", error.getvalue())
        self.assertNotIn("must-not-print", error.getvalue())


@unittest.skipUnless(os.name == "nt" and struct.calcsize("P") == 8,
                     "real native DLL lab requires 64-bit Windows Python")
class NativeServerMainIntegrationTests(unittest.TestCase):
    def test_owned_native_dll_reaches_server_main_and_dashboard(self):
        output = io.StringIO()
        port = fixture.available_port()
        with redirect_stdout(output):
            result = fixture.run_check(port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0,
                                "owned listener must be closed after the check")
        self.assertEqual(result["result"], "PASS")
        self.assertTrue(result["native_lab_only"])
        self.assertFalse(result["game_used"])
        self.assertFalse(result["launcher_used"])
        self.assertEqual(result["stored_events"], result["acknowledged_events"])
        self.assertEqual(result["stored_events"], result["added_events"] + 2)
        self.assertTrue(result["selected_dll_detected_once"])
        self.assertEqual(result["repeat_added_events"], 0)
        self.assertEqual(result["signature_status"], "trusted")
        self.assertEqual(result["raw_score"], 1)
        self.assertEqual(result["central_assessment"], "INCONCLUSIVE")
        self.assertFalse(result["assessment_complete"])
        self.assertTrue(result["scoring_history_preserved"])
        self.assertNotIn("target_pid", json.dumps(result))
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
