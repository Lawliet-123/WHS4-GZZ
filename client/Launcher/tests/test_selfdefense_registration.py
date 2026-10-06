"""Launcher wiring for SelfDefense, independent of a real game or server."""

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import nullcontext
from dataclasses import replace
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

    def test_antidebug_is_a_separate_continuous_pre_game_module(self):
        observer = modules.by_name()["selfdefense_anti_debug"]
        self.assertEqual(observer.owner, "4번 (성민)")
        self.assertEqual(observer.mode, modules.CONTINUOUS)
        self.assertFalse(observer.needs_game)
        self.assertFalse(observer.needs_admin)
        self.assertTrue(observer.restart)
        self.assertEqual(observer.stop_grace_s, 30.0)
        self.assertEqual(observer.session_log_dir,
                         "client/SelfDefense/anti_debug/logs")
        self.assertTrue(os.path.isfile(observer.script_path()))
        argv = observer.resolved({
            "session": "normal_001", "player": "player_042",
            "t0": "1000.250", "telemetry": "managed",
        })
        self.assertEqual(argv[1], "client/SelfDefense/anti_debug/main.py")
        for option, value in (("--session-id", "normal_001"),
                              ("--player-id", "player_042"),
                              ("--t0", "1000.250"),
                              ("--telemetry", "managed")):
            self.assertEqual(argv[argv.index(option) + 1], value)
        self.assertNotIn("--once", argv)
        self.assertNotIn("--self-only", argv)

    def test_antidebug_state_is_included_in_launcher_heartbeat(self):
        observer = modules.by_name()["selfdefense_anti_debug"]
        with tempfile.TemporaryDirectory() as directory, (
            mock.patch.object(process_manager, "LOG_DIR", directory)
        ), mock.patch.object(process_manager, "is_admin", return_value=False), (
            mock.patch.object(process_manager.registry, "begin_session")
        ):
            manager = process_manager.ProcessManager(
                [observer], "normal_001", "player_042", 1000.25
            )
        state = manager.states[observer.name]
        self.assertEqual(state.status, process_manager.PENDING)
        status, required, pid, details = launcher_heartbeat.component_of(state)
        self.assertTrue(required)
        self.assertIsNone(pid)
        self.assertEqual(details["launcher_status"], process_manager.PENDING)

    def test_launcher_starts_antidebug_with_the_shared_session(self):
        observer = modules.by_name()["selfdefense_anti_debug"]
        proc = mock.Mock(pid=7654)
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory, (
            mock.patch.object(process_manager, "LOG_DIR", directory)
        ), mock.patch.object(process_manager, "is_admin", return_value=False), (
            mock.patch.object(process_manager.registry, "begin_session")
        ), mock.patch.object(process_manager.registry, "lock",
                             return_value=nullcontext()), (
            mock.patch.object(process_manager.registry, "live_pid", return_value=None)
        ), mock.patch.object(process_manager.registry, "spawn",
                             return_value=proc) as spawn, (
            mock.patch.object(process_manager.registry, "register")
        ) as register_call:
            manager = process_manager.ProcessManager(
                [observer], "normal_001", "player_042", 1000.25
            )
            self.assertTrue(manager.start(observer.name))
            state = manager.states[observer.name]
            self.assertEqual(state.status, process_manager.RUNNING)
            status, required, pid, details = launcher_heartbeat.component_of(state)
            self.assertEqual((status, required, pid), ("running", True, 7654))
            self.assertEqual(details["launcher_status"], process_manager.RUNNING)
        argv, cwd, log_path = spawn.call_args.args[:3]
        self.assertEqual(cwd, modules.REPO)
        self.assertEqual(argv[argv.index("--session-id") + 1], "normal_001")
        self.assertEqual(argv[argv.index("--player-id") + 1], "player_042")
        self.assertEqual(argv[argv.index("--t0") + 1], "1000.250")
        self.assertEqual(argv[argv.index("--telemetry") + 1], "off")
        self.assertTrue(log_path.endswith("selfdefense_anti_debug.log"))
        self.assertTrue(register_call.call_args.kwargs["restartable"])

    @unittest.skipUnless(os.name == "nt", "Windows process registry")
    def test_antidebug_reads_a_real_launcher_registry_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "observer"
            observer = replace(
                modules.by_name()["selfdefense_anti_debug"],
                argv=modules.by_name()["selfdefense_anti_debug"].argv + [
                    "--interval", "0.05", "--duration", "0.6",
                    "--output-dir", str(output),
                ],
            )
            log_dir = str(Path(directory) / "launcher")
            registry = process_manager.registry
            with mock.patch.dict(os.environ, {
                "AC_LAUNCHER_LOG_DIR": log_dir,
                "GZZ_TELEMETRY_URL": "",
            }), mock.patch.object(process_manager, "LOG_DIR", log_dir), (
                mock.patch.object(registry, "LOG_DIR", log_dir)
            ), mock.patch.object(
                registry, "PID_FILE", str(Path(log_dir) / "anticheat_pids.json")
            ), mock.patch.object(
                registry, "LOCK_DIR", str(Path(log_dir) / "locks")
            ):
                manager = process_manager.ProcessManager(
                    [observer], "normal_antidebug_001", "player_042", time.time()
                )
                try:
                    self.assertTrue(manager.start(observer.name))
                    self.assertEqual(
                        manager.states[observer.name].proc.wait(timeout=10), 0
                    )
                finally:
                    manager.stop_all(grace_s=1.0)
            events = [json.loads(line) for path in output.rglob("events.jsonl")
                      for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(events)
            self.assertTrue(any(event["evidence"]["registry_state"] == "VALID"
                                for event in events))
            self.assertTrue(all(event["evidence"]["scope"] == "launcher_registered"
                                for event in events))

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
