"""Observation-contract fixtures; no live sensors or real captures are used."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anti_esp.config import HandleMonitorSettings, ModuleMonitorSettings, Settings, TelemetrySettings
from anti_esp.controller import AntiEspController
from anti_esp.core.events import SensorBatch
from anti_esp.core.session import SessionTelemetryWriter
from anti_esp.store import SQLiteEvidenceStore
from anti_esp.windows_api import ProcessInfo


class FixtureSensor:
    def __init__(self, sensor_id, *, status="online", details=None):
        self.sensor_id = sensor_id
        self.status = status
        self.details = details or {}

    def poll(self, _context):
        return SensorBatch(self.sensor_id, self.status, details=self.details)

    def reset_cooldowns(self):
        pass

    def reset_baseline(self):
        pass


class ObservationCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def make_controller(self, *, elevated=True, created_at=100.0, sysmon_details=None,
                        handles_details=None, multiple=False):
        self.now = 200.0
        self.created_at = created_at
        self.sysmon = FixtureSensor("sysmon_process_access", details=sysmon_details)
        self.handles = FixtureSensor("current_process_handles", details=handles_details)
        settings = Settings(
            module_monitor=ModuleMonitorSettings(enabled=True, scan_interval_seconds=30),
            handle_monitor=HandleMonitorSettings(enabled=True),
            telemetry=TelemetrySettings(
                enabled=True, root=self.root / "sessions", session_id="observation_fixture_001",
                player_id="player_fixture_001", scenario="normal",
            ),
        )

        def processes(_name):
            result = [ProcessInfo(777, r"C:\Private\fixture-game.exe", created_at=self.created_at)]
            if multiple:
                result.append(ProcessInfo(888, r"C:\Private\fixture-game.exe", created_at=101.0))
            return result

        return AntiEspController(
            settings, clock=lambda: self.now, session_started_at=200.0,
            process_provider=processes, elevation_provider=lambda: elevated,
            store=SQLiteEvidenceStore(":memory:"),
            process_access_sensor=self.sysmon, handle_sensor=self.handles,
            module_sensor=FixtureSensor("loaded_modules"),
            window_sensor=FixtureSensor("window_overlap"),
        )

    def run_polls(self, controller, *, running=True, change_instance=False):
        with patch.object(AntiEspController, "running", new_callable=PropertyMock, return_value=running):
            controller.poll_once()
            self.now += 5
            if change_instance:
                self.created_at += 1
            controller.poll_once()
        controller.close()
        return json.loads((controller.telemetry_session_dir / "manifest.json").read_text("utf-8"))

    def test_actual_poll_boundary_records_health_and_fresh_sensor_coverage(self):
        controller = self.make_controller()
        manifest = self.run_polls(controller)
        self.assertEqual(manifest["player_id"], "player_fixture_001")
        self.assertEqual(manifest["event_count"], 0)
        self.assertEqual(manifest["raw_event_count"], 0)
        self.assertEqual(manifest["raw_counts"], {})
        summary = manifest["observation_summary"]
        self.assertEqual((summary["poll_count"], summary["healthy_poll_count"], summary["insufficient_poll_count"]),
                         (2, 2, 0))
        self.assertEqual((summary["first_poll_ms"], summary["last_poll_ms"]), (0, 5000))
        self.assertEqual(summary["last_status"], "LOW")
        self.assertFalse(summary["game_instance_changed"])
        self.assertRegex(summary["game_instance_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(summary["required_sensors"]["sysmon"]["online_observed_count"], 2)
        self.assertEqual(summary["required_sensors"]["modules"]["online_observed_count"], 1)
        self.assertEqual(summary["required_sensors"]["modules"]["online_poll_count"], 2)
        self.assertNotIn("C:\\Private", json.dumps(summary))
        self.assertNotIn("777", json.dumps(summary).replace(summary["game_instance_sha256"], ""))
        self.assertEqual(list((controller.telemetry_session_dir / "raw").iterdir()), [])
        from ReplayAnalyzer.tools.export_esp_replay import export_session
        exported = export_session(controller.telemetry_session_dir, self.root / "replays" / "esp")
        self.assertEqual((exported / "events.jsonl").read_bytes(), b"")

    def test_manual_poll_or_missing_privilege_never_proves_healthy_empty(self):
        controller = self.make_controller()
        summary = self.run_polls(controller, running=False)["observation_summary"]
        self.assertEqual((summary["healthy_poll_count"], summary["insufficient_poll_count"]), (0, 2))
        # Use a separate fixture root; session IDs never append to an old run.
        self.root = self.root / "no-admin"
        controller = self.make_controller(elevated=False)
        summary = self.run_polls(controller)["observation_summary"]
        self.assertEqual(summary["healthy_poll_count"], 0)
        self.assertEqual(summary["last_status"], "INSUFFICIENT")

    def test_missing_birth_time_or_multiple_targets_disqualifies_identity(self):
        for created_at, multiple in ((None, False), (100.0, True)):
            with self.subTest(created_at=created_at, multiple=multiple):
                self.root = Path(self.temporary.name) / str(multiple)
                controller = self.make_controller(created_at=created_at, multiple=multiple)
                summary = self.run_polls(controller)["observation_summary"]
                self.assertEqual(summary["game_instance_missing_poll_count"], 2)
                self.assertEqual(summary["healthy_poll_count"], 0)

    def test_same_pid_with_changed_birth_time_retains_changed_flag(self):
        controller = self.make_controller()
        manifest = self.run_polls(controller, change_instance=True)
        self.assertTrue(manifest["observation_summary"]["game_instance_changed"])
        from ReplayAnalyzer.tools.export_esp_replay import export_session
        with self.assertRaisesRegex(ValueError, "unchanged game instance"):
            export_session(controller.telemetry_session_dir, self.root / "replays" / "esp")

    def test_online_but_partial_collection_disqualifies_only_capture_proof(self):
        for name in ("sysmon", "handles"):
            with self.subTest(sensor=name):
                self.root = Path(self.temporary.name) / name
                controller = self.make_controller(
                    sysmon_details={"truncated": name == "sysmon"},
                    handles_details={"truncated": name == "handles"},
                )
                with patch.object(AntiEspController, "running", new_callable=PropertyMock, return_value=True):
                    controller.poll_once()
                    self.assertEqual(controller.snapshot()["status"], "LOW")
                    self.assertEqual(controller.sensor_status()[name]["status"], "online")
                controller.close()
                manifest = json.loads((controller.telemetry_session_dir / "manifest.json").read_text("utf-8"))
                summary = manifest["observation_summary"]
                self.assertEqual(summary["healthy_poll_count"], 0)
                self.assertEqual(summary["last_status"], "INSUFFICIENT")
                self.assertEqual(summary["required_sensors"][name]["last_status"], "unavailable")

    def test_writer_counts_missing_and_regressing_poll_metadata_without_raw(self):
        with SessionTelemetryWriter(self.root, session_id="writer_fixture", game_executable="fixture.exe",
                                    player_id="player_fixture") as writer:
            arguments = dict(status="LOW", observation_confidence=100, minimum_observation_confidence=60,
                             required_sensor_status={name: "online" for name in
                                                     ("collector", "game", "privilege", "sysmon")},
                             observed_sensors={"collector", "game", "privilege", "sysmon"})
            writer.record_observation(timestamp_ms=1000, game_instance_sha256=None, **arguments)
            writer.record_observation(timestamp_ms=999, game_instance_sha256="a" * 64, **arguments)
        summary = json.loads((writer.session_dir / "manifest.json").read_text("utf-8"))["observation_summary"]
        self.assertEqual(summary["game_instance_missing_poll_count"], 1)
        self.assertEqual(summary["timestamp_regression_count"], 1)
        self.assertEqual((summary["healthy_poll_count"], summary["insufficient_poll_count"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
