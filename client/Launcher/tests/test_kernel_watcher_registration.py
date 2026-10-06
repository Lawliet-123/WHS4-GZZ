"""KernelSentinel Launcher command contract; never loads a kernel driver."""

import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock


LAUNCHER_DIR = Path(__file__).resolve().parents[1]
if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import modules  # noqa: E402
import process_manager  # noqa: E402
import main as launcher_main  # noqa: E402


class _FakeProcess:
    pid = 7654

    @staticmethod
    def poll():
        return None


class KernelWatcherRegistrationTests(unittest.TestCase):
    def test_module_uses_agent_package_from_its_own_working_directory(self):
        watcher = modules.by_name()["kernel_watcher"]
        self.assertTrue(watcher.needs_game)
        self.assertTrue(watcher.needs_admin)
        self.assertFalse(watcher.restart)
        self.assertEqual(watcher.mode, modules.CONTINUOUS)
        self.assertEqual(
            watcher.cwd,
            os.path.join(
                modules.REPO, "client", "KernelSentinelValidation-github",
                "KernelSentinel",
            ),
        )
        self.assertEqual(
            watcher.script_path(), os.path.join(watcher.cwd, "agent", "main.py")
        )
        self.assertTrue(os.path.isfile(watcher.script_path()))
        self.assertTrue(os.path.isfile(os.path.join(watcher.cwd, "config", "policy.json")))
        self.assertEqual(
            watcher.session_log_dir,
            "client/KernelSentinelValidation-github/KernelSentinel/runs",
        )

    def test_resolved_command_is_observe_only_and_local_only(self):
        watcher = modules.by_name()["kernel_watcher"]
        argv = watcher.resolved({
            "game_pid": 9876, "session": "normal_001", "player": "player_042",
        })
        self.assertEqual(argv[1:4], ["-m", "agent.main", "watch"])
        self.assertEqual(argv[argv.index("--pid") + 1], "9876")
        self.assertEqual(argv[argv.index("--mode") + 1], "observe")
        self.assertEqual(argv[argv.index("--config") + 1], "config/policy.json")
        self.assertEqual(argv[argv.index("--session-id") + 1], "normal_001")
        self.assertEqual(argv[argv.index("--player-id") + 1], "player_042")
        self.assertEqual(argv[argv.index("--out") + 1], "runs/normal_001")
        if modules.kernel_thread_options():
            self.assertEqual(argv[argv.index("--thread-interval") + 1], "0")
        else:
            self.assertNotIn("--thread-interval", argv)
        self.assertNotIn("--t0", argv)
        self.assertNotIn("--telemetry", argv)

    def test_thread_sensor_is_disabled_outside_its_supported_build(self):
        with mock.patch.object(
            modules.sys, "getwindowsversion", return_value=mock.Mock(build=19045),
            create=True,
        ):
            self.assertEqual(modules.kernel_thread_options(), [])
        with mock.patch.object(
            modules.sys, "getwindowsversion", return_value=mock.Mock(build=26100),
            create=True,
        ):
            self.assertEqual(
                modules.kernel_thread_options(), ["--thread-interval", "0"]
            )

    def test_process_manager_passes_pid_and_working_directory(self):
        watcher = modules.by_name()["kernel_watcher"]
        with tempfile.TemporaryDirectory() as directory, (
            mock.patch.object(process_manager, "LOG_DIR", directory)
        ), mock.patch.object(process_manager, "is_admin", return_value=True), (
            mock.patch.object(process_manager.registry, "begin_session")
        ), mock.patch.object(
            process_manager.registry, "lock", return_value=nullcontext()
        ), mock.patch.object(
            process_manager.registry, "spawn", return_value=_FakeProcess()
        ) as spawn, mock.patch.object(process_manager.registry, "register"):
            manager = process_manager.ProcessManager(
                [watcher], "normal_001", "player_042", 1000.25
            )
            self.assertEqual(
                manager.states["kernel_watcher"].status, process_manager.PENDING
            )
            manager.set_game_pid(9876)
            self.assertTrue(manager.start("kernel_watcher"))
        argv, cwd = spawn.call_args.args[:2]
        self.assertEqual(argv[argv.index("--pid") + 1], "9876")
        self.assertEqual(cwd, watcher.cwd)

    def test_existing_output_directory_blocks_same_session_reuse(self):
        watcher = modules.by_name()["kernel_watcher"]
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / watcher.session_log_dir / "normal_001"
            existing.mkdir(parents=True)
            with mock.patch.object(launcher_main, "REPO", directory):
                found = launcher_main.existing_sessions([watcher], "normal_001")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].endswith(os.path.join("runs", "normal_001")))


if __name__ == "__main__":
    unittest.main()
