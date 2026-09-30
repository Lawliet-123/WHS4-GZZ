"""AutoPaint/shared integration with synthetic sensors and a loopback receiver."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from gzz_anticheat import cli
from shared import logger
from shared.config import ClientConfig
from shared.errors import ConfigurationError, QueueFullError, StorageError
from test_shared import LocalReceiver


ROOT = Path(__file__).resolve().parents[1]
NORMAL = {"target": {"found": True}, "sensor_healthy": True}


class TelemetryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gzz-autopaint-integration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(logger.shutdown_client)
        logger.shutdown_client()
        clean = {k: v for k, v in os.environ.items() if not k.startswith("GZZ_TELEMETRY_")}
        env_patch = patch.dict(os.environ, clean, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.environment = {
            "GZZ_TELEMETRY_URL": "http://127.0.0.1:9",
            "GZZ_TELEMETRY_TOKEN": "test-token",
            "GZZ_TELEMETRY_OUTBOX": str(self.root / "outbox.sqlite3"),
            "GZZ_TELEMETRY_ALLOW_INSECURE_LOOPBACK": "true",
            "GZZ_TELEMETRY_MAX_ATTEMPTS": "2",
            "GZZ_TELEMETRY_TIMEOUT_SECONDS": "0.2",
            "GZZ_TELEMETRY_RETRY_BASE_SECONDS": "0.01",
            "GZZ_TELEMETRY_RETRY_MAX_SECONDS": "0.02",
            "GZZ_TELEMETRY_RETRY_JITTER_RATIO": "0",
        }
        self.stdout, self.stderr = io.StringIO(), io.StringIO()

    def run_detector(self, snapshots, *, mode=None, entrypoint=cli.main):
        counter = [0]
        stream = iter(snapshots)

        class FakeSensor:
            def __init__(self, *args, **kwargs):
                pass

            def collect(self):
                counter[0] += 1
                item = next(stream)
                if isinstance(item, BaseException):
                    raise item
                return copy.deepcopy(item)

        args = ["--session-id", "integration", "--player-id", "player_test",
                "--output-dir", str(self.root / "logs"), "--duration", str(len(snapshots)),
                "--interval", "0.001"]
        if mode is not None:
            args += ["--telemetry", mode]
        with patch.dict(sys.modules, {"gzz_anticheat.windows_sensor": types.SimpleNamespace(WindowsClientSensor=FakeSensor)}), \
             patch("gzz_anticheat.session.Session.elapsed_ms", side_effect=lambda: counter[0] * 1000), \
             patch("gzz_anticheat.session.Session.clock_drift_ms", return_value=0), \
             contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            return entrypoint(args)

    def events(self):
        path = self.root / "logs/integration/events.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def manifest(self):
        return json.loads((self.root / "logs/integration/manifest.json").read_text(encoding="utf-8"))

    def test_every_event_including_repeated_zero_is_local_first_and_sent_unchanged(self):
        cheat = json.loads((ROOT / "tests/fixtures/autopaint_001.raw.jsonl").read_text().splitlines()[0])
        cheat.update(target={"found": True}, sensor_healthy=True)
        forwarded = []
        send = logger.send_detection

        def observe_local_first(result):
            self.assertEqual(self.events()[-1], result)
            forwarded.append(copy.deepcopy(result))
            return send(result)

        with LocalReceiver(self.root / "server") as receiver:
            self.environment["GZZ_TELEMETRY_URL"] = receiver.url
            with patch.dict(os.environ, self.environment), \
                 patch.object(logger, "configure_client", wraps=logger.configure_client) as configure, \
                 patch.object(logger, "send_detection", side_effect=observe_local_first), \
                 patch.object(logger, "flush_client", wraps=logger.flush_client) as flush, \
                 patch.object(logger, "shutdown_client", wraps=logger.shutdown_client) as shutdown:
                self.assertEqual(self.run_detector([NORMAL, NORMAL, cheat]), 0)
                configure.assert_called_once()
                flush.assert_called_once()
                shutdown.assert_called_once()
            stored = [row.result for row in receiver.writer.iter_stored()]
        self.assertEqual(forwarded, self.events())
        self.assertEqual(stored, self.events())
        self.assertEqual([row["timestamp_ms"] for row in stored], [1000, 2000, 3000])
        self.assertEqual([row["raw_score"] for row in stored[:2]], [0, 0])
        self.assertGreaterEqual(stored[-1]["raw_score"], 10)
        self.assertEqual(len(self.stdout.getvalue().splitlines()), 2)  # Console dedup does not filter sends.
        self.assertTrue(all(len(row) == 7 for row in stored))
        self.assertIn("flushed=True stopped=True", self.stderr.getvalue())
        self.assertNotIn("test-token", self.stderr.getvalue())

    def test_lost_ack_retries_same_id_without_duplicate_storage(self):
        with LocalReceiver(self.root / "server", ["store_then_503"]) as receiver:
            self.environment["GZZ_TELEMETRY_URL"] = receiver.url
            with patch.dict(os.environ, self.environment):
                self.assertEqual(self.run_detector([NORMAL]), 0)
            self.assertEqual(len(receiver.requests), 2)
            self.assertEqual(receiver.requests[0][0], receiver.requests[1][0])
            self.assertEqual([row.result for row in receiver.writer.iter_stored()], self.events())

    def test_auth_failure_preserves_local_event_and_failed_outbox(self):
        with LocalReceiver(self.root / "server", ["401"]) as receiver:
            self.environment["GZZ_TELEMETRY_URL"] = receiver.url
            with patch.dict(os.environ, self.environment):
                self.assertEqual(self.run_detector([NORMAL]), 0)
                client = logger.configure_client(ClientConfig.from_env())
                self.assertEqual(client.status().failed, 1)
                self.assertEqual(client.failures()[0]["last_error"], "http_401")
                logger.shutdown_client()
            self.assertEqual(len(receiver.requests), 1)
        self.assertEqual(len(self.events()), 1)
        self.assertIn("flushed=False", self.stderr.getvalue())
        self.assertIn("delivery not confirmed", self.stderr.getvalue())

    def test_enqueue_rejection_keeps_local_records_and_continues(self):
        with patch.dict(os.environ, self.environment), \
             patch.object(logger, "send_detection", side_effect=QueueFullError("private-error-detail")):
            self.assertEqual(self.run_detector([NORMAL, NORMAL]), 0)
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(self.manifest()["status"], "COMPLETED")
        self.assertIn("enqueue_failed=2", self.stderr.getvalue())
        self.assertNotIn("private-error-detail", self.stderr.getvalue())

    def test_ctrl_c_flushes_and_shuts_down_sender(self):
        with LocalReceiver(self.root / "server") as receiver:
            self.environment["GZZ_TELEMETRY_URL"] = receiver.url
            with patch.dict(os.environ, self.environment):
                self.assertEqual(self.run_detector([NORMAL, KeyboardInterrupt()]), 0)
            self.assertEqual([row.result for row in receiver.writer.iter_stored()], self.events())
        self.assertEqual(self.manifest()["status"], "STOPPED")
        with self.assertRaises(ConfigurationError):
            logger.get_client_status()

    def test_sensor_error_still_flushes_previous_event_and_shuts_down(self):
        with LocalReceiver(self.root / "server") as receiver:
            self.environment["GZZ_TELEMETRY_URL"] = receiver.url
            with patch.dict(os.environ, self.environment):
                self.assertEqual(self.run_detector([NORMAL, RuntimeError("synthetic sensor error")]), 2)
            self.assertEqual([row.result for row in receiver.writer.iter_stored()], self.events())
        self.assertEqual(self.manifest()["status"], "ERROR")
        with self.assertRaises(ConfigurationError):
            logger.get_client_status()

    def test_local_write_failure_does_not_send_and_still_cleans_up(self):
        from gzz_anticheat.logging_io import JsonlWriter
        write = JsonlWriter.write

        def fail_events(writer, result):
            if writer.path.name == "events.jsonl":
                raise OSError("synthetic disk failure")
            return write(writer, result)

        with patch.dict(os.environ, self.environment), \
             patch.object(JsonlWriter, "write", fail_events), \
             patch.object(logger, "send_detection") as send:
            self.assertEqual(self.run_detector([NORMAL]), 2)
        send.assert_not_called()
        self.assertEqual(self.events(), [])
        with self.assertRaises(ConfigurationError):
            logger.get_client_status()

    def test_target_missing_or_unhealthy_does_not_fabricate_events(self):
        with patch.dict(os.environ, self.environment), patch.object(logger, "send_detection") as send:
            self.assertEqual(self.run_detector([
                {"target": {"found": False}}, {"target": {"found": True}, "sensor_healthy": False}]), 0)
        send.assert_not_called()
        self.assertEqual(self.events(), [])

    def test_managed_without_settings_fails_before_creating_a_session(self):
        self.assertEqual(self.run_detector([NORMAL]), 2)
        self.assertFalse((self.root / "logs").exists())
        self.assertIn("telemetry startup failed", self.stderr.getvalue())

    def test_existing_session_error_does_not_leak_a_sender_or_overwrite_logs(self):
        previous = self.root / "logs/integration"
        previous.mkdir(parents=True)
        marker = previous / "events.jsonl"
        marker.write_text("previous session\n", encoding="utf-8")
        with patch.dict(os.environ, self.environment), \
             patch.object(logger, "shutdown_client", wraps=logger.shutdown_client) as shutdown:
            self.assertEqual(self.run_detector([NORMAL]), 2)
            shutdown.assert_called_once()
        self.assertEqual(marker.read_text(encoding="utf-8"), "previous session\n")

    def test_off_mode_does_not_initialize_or_send(self):
        with patch.object(logger, "configure_client") as configure, \
             patch.object(logger, "send_detection") as send:
            self.assertEqual(self.run_detector([NORMAL], mode="off"), 0)
        configure.assert_not_called()
        send.assert_not_called()
        self.assertEqual(len(self.events()), 1)

    def test_external_mode_uses_but_does_not_close_app_owned_sender(self):
        with LocalReceiver(self.root / "server") as receiver:
            self.environment["GZZ_TELEMETRY_URL"] = receiver.url
            with patch.dict(os.environ, self.environment):
                client = logger.configure_client(ClientConfig.from_env())
                with patch.object(logger, "configure_client") as configure, \
                     patch.object(logger, "flush_client") as flush, \
                     patch.object(logger, "shutdown_client") as shutdown:
                    self.assertEqual(self.run_detector([NORMAL], mode="external"), 0)
                    configure.assert_not_called()
                    flush.assert_not_called()
                    shutdown.assert_not_called()
                self.assertTrue(client.status().worker_alive)
                self.assertTrue(logger.flush_client(2))
                self.assertEqual([row.result for row in receiver.writer.iter_stored()], self.events())
                logger.shutdown_client()

    def test_external_without_app_sender_fails_before_session_creation(self):
        self.assertEqual(self.run_detector([NORMAL], mode="external"), 2)
        self.assertFalse((self.root / "logs").exists())

    def test_managed_refuses_second_owner_without_closing_existing_sender(self):
        with patch.dict(os.environ, self.environment):
            client = logger.configure_client(ClientConfig.from_env())
            self.assertEqual(self.run_detector([NORMAL]), 2)
            self.assertTrue(client.status().worker_alive)
            self.assertFalse((self.root / "logs").exists())

    def test_flush_error_or_second_ctrl_c_still_attempts_shutdown(self):
        for error in (StorageError("private-error-detail"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), patch.dict(os.environ, self.environment), \
                 patch.object(logger, "flush_client", side_effect=error), \
                 patch.object(logger, "shutdown_client", wraps=logger.shutdown_client) as shutdown:
                # No session files here: test the cleanup path without recreating the same session.
                from gzz_anticheat.telemetry import TelemetryForwarder
                with contextlib.redirect_stderr(self.stderr), TelemetryForwarder("managed"):
                    pass
                shutdown.assert_called_once()
        self.assertNotIn("private-error-detail", self.stderr.getvalue())

    def test_main_py_runs_detector_and_import_does_not_start_sender(self):
        spec = importlib.util.spec_from_file_location("autopaint_entry_for_test", ROOT / "main.py")
        module = importlib.util.module_from_spec(spec)
        with patch.object(logger, "configure_client") as configure:
            spec.loader.exec_module(module)
            configure.assert_not_called()
        self.assertEqual(self.run_detector([NORMAL], mode="off", entrypoint=module.main), 0)

    def test_main_help_runs_from_another_working_directory_without_settings(self):
        result = subprocess.run([sys.executable, str(ROOT / "main.py"), "--help"],
                                cwd=self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--telemetry", result.stdout)
        self.assertFalse((self.root / "telemetry-outbox").exists())


if __name__ == "__main__":
    unittest.main()
