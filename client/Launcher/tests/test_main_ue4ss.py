"""런처 본체에서 UE4SS 준비·로드 확인을 실제 실행 순서에 연결하는 테스트."""

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


LAUNCHER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAUNCHER))

import game_launcher
import main as launcher_main
from modules import by_name


class UE4SSMainTests(unittest.TestCase):
    def test_non_ue4ss_only_run_does_not_touch_game_files(self):
        with mock.patch.object(game_launcher, "prepare_ue4ss") as prepare:
            result = launcher_main._prepare_ue4ss(
                [by_name()["input_signature"]], "C:/game", None)
        self.assertIsNone(result)
        prepare.assert_not_called()

    def test_running_game_is_checked_without_changes(self):
        expected = game_launcher.UE4SSResult("READY", "already installed")
        with mock.patch.object(game_launcher, "prepare_ue4ss", return_value=expected) as prepare:
            result = launcher_main._prepare_ue4ss(
                [by_name()["autopaint"]], "C:/game", 321)
        self.assertIs(result, expected)
        prepare.assert_called_once_with("C:/game", game_running=True)

    def test_missing_game_root_is_not_treated_as_ready(self):
        with mock.patch.object(game_launcher, "prepare_ue4ss") as prepare:
            result = launcher_main._prepare_ue4ss(
                [by_name()["autopaint"]], None, None)
        self.assertEqual(result.status, "MISSING")
        prepare.assert_not_called()

    def test_no_game_rechecks_liveness_during_prepare(self):
        with mock.patch.object(game_launcher, "prepare_ue4ss") as prepare:
            launcher_main._prepare_ue4ss([by_name()["aimbot"]], "C:/game", None)
        prepare.assert_called_once_with("C:/game", game_running=None)

    def test_main_prepares_before_modules_and_checks_session_after_autopaint(self):
        calls = []
        module = by_name()["autopaint"]
        state = SimpleNamespace(name="autopaint", module=module, status="PENDING")

        class FakeManager:
            def __init__(self, *args, **kwargs):
                self.states = {"autopaint": state}

            def start_group(self, needs_game):
                calls.append(("start_group", needs_game))
                if needs_game:
                    state.status = "RUNNING"

            def set_game_pid(self, pid):
                self.game_pid = pid

            def poll(self):
                pass

            def snapshot(self):
                return []

            def final_targets(self):
                return []

            def stop_all(self):
                return {}

        ready = game_launcher.UE4SSResult("READY", "files ready")
        loaded = game_launcher.UE4SSResult("READY", "session loaded")

        def prepare(*args, **kwargs):
            calls.append(("prepare", args, kwargs))
            return ready

        def wait_load(*args, **kwargs):
            calls.append(("wait_load", args, kwargs))
            return loaded

        rendered = []
        with mock.patch.object(launcher_main, "resolve_player_id", return_value=("player_1", "test", False)), \
             mock.patch.object(launcher_main, "existing_sessions", return_value=[]), \
             mock.patch.object(launcher_main, "ProcessManager", FakeManager), \
             mock.patch.object(launcher_main, "preflight"), \
             mock.patch.object(launcher_main, "publish_game_dir", return_value="C:/game"), \
             mock.patch.object(launcher_main, "_game_start_epoch", return_value=1000.0), \
             mock.patch.object(launcher_main.game_launcher, "find_game_pid", side_effect=[None, None, None]), \
             mock.patch.object(launcher_main.game_launcher, "prepare_ue4ss", side_effect=prepare), \
             mock.patch.object(launcher_main.game_launcher, "launch", return_value=True), \
             mock.patch.object(launcher_main.game_launcher, "wait_for_game", return_value=321), \
             mock.patch.object(launcher_main.game_launcher, "wait_for_ue4ss_log", side_effect=wait_load), \
             mock.patch.object(launcher_main.ui, "line"), \
             mock.patch.object(launcher_main.ui, "render", side_effect=lambda rows, ctx: rendered.append(dict(ctx))):
            code = launcher_main.main(["--session", "ue4ss_test", "--only", "autopaint"])

        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0], "prepare")
        self.assertEqual(calls[1:3], [("start_group", False), ("start_group", True)])
        self.assertEqual(calls[3][0], "wait_load")
        self.assertEqual(calls[3][1], ("C:/game", "ue4ss_test", 1000.0))
        self.assertEqual(rendered[-1]["ue4ss"], "READY: session loaded")


if __name__ == "__main__":
    unittest.main()
