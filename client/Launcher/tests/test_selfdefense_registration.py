"""Launcher wiring for SelfDefense, independent of a real game or server."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


LAUNCHER_DIR = Path(__file__).resolve().parents[1]
if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import modules  # noqa: E402
import process_manager  # noqa: E402
import launcher_heartbeat  # noqa: E402


class SelfDefenseRegistrationTests(unittest.TestCase):
    def test_watchdog_keeps_existing_name_and_common_clock(self):
        watchdog = modules.by_name()["self_defense"]
        argv = watchdog.resolved({
            "session": "normal_001", "player": "player_042",
            "t0": "1000.250", "telemetry": "off",
        })
        self.assertEqual(watchdog.mode, modules.CONTINUOUS)
        self.assertFalse(watchdog.needs_game)
        self.assertTrue(watchdog.restart)
        self.assertIn("client/SelfDefense/watchdog/main.py", argv)
        self.assertEqual(argv[argv.index("--t0") + 1], "1000.250")

    def test_integrity_without_release_pin_is_visible_but_not_started(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            integrity = modules.selfdefense_integrity_module()
        self.assertTrue(integrity.disabled_reason)
        with tempfile.TemporaryDirectory() as directory, (
            mock.patch.object(process_manager, "LOG_DIR", directory)
        ), mock.patch.object(process_manager, "is_admin", return_value=True), (
            mock.patch.object(process_manager.registry, "begin_session")
        ), mock.patch.object(process_manager.registry, "spawn") as spawn:
            manager = process_manager.ProcessManager(
                [integrity], "normal_001", "player_042", 1000.25
            )
            self.assertEqual(
                manager.states["selfdefense_integrity"].status,
                process_manager.SKIPPED,
            )
            heartbeat_status, required, pid, details = (
                launcher_heartbeat.component_of(
                    manager.states["selfdefense_integrity"]
                )
            )
            self.assertEqual(heartbeat_status, "stopped")
            self.assertFalse(required)
            self.assertIsNone(pid)
            self.assertEqual(details["launcher_status"], "SKIPPED")
            self.assertFalse(manager.start("selfdefense_integrity"))
            spawn.assert_not_called()

    def test_integrity_receives_pinned_release_paths_and_common_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = str(Path(directory) / "release")
            baseline = str(Path(directory) / "approved.json")
            pin = "a" * 64
            with mock.patch.dict(os.environ, {
                "GZZ_INTEGRITY_ROOT": root,
                "GZZ_INTEGRITY_BASELINE": baseline,
                "GZZ_INTEGRITY_BASELINE_SHA256": pin,
            }, clear=True):
                integrity = modules.selfdefense_integrity_module()
        self.assertFalse(integrity.disabled_reason)
        self.assertEqual(integrity.name, "selfdefense_integrity")
        self.assertEqual(integrity.mode, modules.CONTINUOUS)
        self.assertFalse(integrity.needs_game)
        self.assertTrue(integrity.restart)
        self.assertEqual(integrity.stop_grace_s, 30.0)
        argv = integrity.resolved({
            "session": "normal_001", "player": "player_042",
            "t0": "1000.250", "telemetry": "off",
        })
        self.assertEqual(argv[argv.index("--root") + 1], root)
        self.assertEqual(argv[argv.index("--baseline") + 1], baseline)
        self.assertEqual(argv[argv.index("--baseline-sha256") + 1], pin)
        self.assertEqual(argv[argv.index("--t0") + 1], "1000.250")
        self.assertEqual(argv[argv.index("--telemetry") + 1], "off")

    def test_invalid_pin_does_not_activate_integrity(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {
                "GZZ_INTEGRITY_ROOT": str(Path(directory) / "release"),
                "GZZ_INTEGRITY_BASELINE": str(Path(directory) / "approved.json"),
                "GZZ_INTEGRITY_BASELINE_SHA256": "not-an-approved-digest",
            },
            clear=True,
        ):
            integrity = modules.selfdefense_integrity_module()
        self.assertIn("SHA-256", integrity.disabled_reason)


if __name__ == "__main__":
    unittest.main()
