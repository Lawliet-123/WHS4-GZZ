"""죽은 등록부 항목이 **왜** 안 도는지 말해 주는가.

PID 가 죽은 항목만 보고는 곧 되살아날 것과 끝난 것을 구분할 수 없었다. 등록부로
우리 프로세스를 가려내는 쪽(4번 AntiDebug·1번·커널)이 그걸 읽는다. 더한 칸 셋:

    last_exit        마지막으로 끝났을 때의 종료 코드와 시각
    next_restart_at  되살릴 수 있는 가장 이른 시각 (0 이면 지금 바로)
    stop_reason      STOP_* 중 하나. None 이면 멈춘 게 아니다

**기존 칸은 건드리지 않는다.** AntiDebug 가 pid/create_time 을 엄격하게 읽어서,
비면 등록부 전체를 오류로 본다(#124 registry_reader.py).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

LAUNCHER_DIR = Path(__file__).resolve().parents[1]
if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import registry  # noqa: E402

SLEEP = "import time; time.sleep(60)"
DIE = "raise SystemExit(7)"


class _RegistryTempDir(unittest.TestCase):
    """등록부를 임시 폴더로 옮긴다. 돌고 있는 런처의 등록부를 건드리지 않는다."""

    def setUp(self):
        # ignore_cleanup_errors: 윈도에서 자식이 죽은 직후에도 OS 가 로그 파일
        # 핸들을 잠깐 더 쥐고 있어 폴더 삭제가 WinError 32 로 실패할 때가 있다
        # (전체 묶음으로 돌릴 때 재현). 임시 폴더가 남는 건 시험 결과와 무관한데,
        # 그걸로 시험이 빨간불이 되면 진짜 실패를 묻는다.
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, value in (("LOG_DIR", root), ("PID_FILE", root / "anticheat_pids.json"),
                            ("LOCK_DIR", root / "locks")):
            p = mock.patch.object(registry, name, str(value))
            p.start()
            self.addCleanup(p.stop)
        registry.begin_session("stop_reason_test")
        self.started = []
        self.addCleanup(self._cleanup_session)
        self.log = str(root / "probe.log")

    def _cleanup_session(self):
        for e in (registry.load().get("entries") or {}).values():
            registry.kill(e.get("pid"), e.get("create_time", 0))
        for proc in self.started:
            registry._kill_proc(proc)     # 안 거두면 ResourceWarning 이 쌓인다
        registry.end_session()

    def spawn(self, code=SLEEP, name="probe", restartable=True):
        argv = [sys.executable, "-c", code]
        proc = registry.spawn(argv, os.getcwd(), self.log)
        self.started.append(proc)
        registry.register(name, proc, by="launcher", restartable=restartable,
                          argv=argv, cwd=os.getcwd(), log=self.log)
        return proc

    def entry(self, name="probe"):
        return registry.entry(name)


class FreshEntryTests(_RegistryTempDir):

    def test_running_module_is_not_marked_stopped(self):
        self.spawn()
        e = self.entry()
        self.assertIsNone(e["stop_reason"])
        self.assertEqual(e["next_restart_at"], 0.0)
        self.assertIsNone(e["last_exit"])

    def test_existing_fields_are_untouched(self):
        proc = self.spawn()
        e = self.entry()
        self.assertEqual(e["pid"], proc.pid)
        self.assertTrue(e["create_time"] > 0)
        self.assertEqual(e["started_by"], "launcher")
        self.assertTrue(e["restartable"])
        self.assertFalse(e["gave_up"])
        self.assertEqual(e["restarts"], [])

    def test_registry_file_is_valid_json_with_the_new_fields(self):
        self.spawn()
        d = json.loads(Path(registry.PID_FILE).read_text(encoding="utf-8"))
        self.assertIn("probe", d["entries"])
        self.assertIn("probe", d["modules"])     # 예전 형식도 그대로


class NoteExitTests(_RegistryTempDir):

    def test_exit_code_and_reason_are_recorded(self):
        self.spawn()
        registry.note_exit("probe", code=7, reason=registry.STOP_EXITED)
        e = self.entry()
        self.assertEqual(e["stop_reason"], "exited")
        self.assertEqual(e["last_exit"]["code"], 7)
        self.assertGreater(e["last_exit"]["at"], 0)

    def test_repeating_the_same_note_does_not_rewrite_the_file(self):
        # 런처는 poll 마다 상태를 본다. 그때마다 다시 쓰면 읽는 쪽이 파일을
        # 바꿔 끼우는 순간과 계속 부딪힌다.
        self.spawn()
        registry.note_exit("probe", code=7, reason=registry.STOP_EXITED)
        before = os.stat(registry.PID_FILE).st_mtime_ns
        for _ in range(5):
            registry.note_exit("probe", code=7, reason=registry.STOP_EXITED)
        self.assertEqual(os.stat(registry.PID_FILE).st_mtime_ns, before)

    def test_a_changed_reason_is_written(self):
        self.spawn()
        registry.note_exit("probe", code=7, reason=registry.STOP_EXITED)
        registry.note_exit("probe", reason=registry.STOP_KILLED)
        self.assertEqual(self.entry()["stop_reason"], "killed")
        self.assertEqual(self.entry()["last_exit"]["code"], 7, "코드를 안 주면 지우지 않는다")

    def test_unknown_module_is_not_created(self):
        # 없는 항목을 만들면 pid 없는 항목이 생겨 AntiDebug 가 등록부 전체를 오류로 본다.
        registry.note_exit("nobody", code=1, reason=registry.STOP_EXITED)
        self.assertIsNone(registry.entry("nobody"))


class RestartTests(_RegistryTempDir):

    def test_backoff_deadline_is_published_and_matches_the_decision(self):
        """기다리는 동안 '언제부터' 를 읽을 수 있고, 그 값이 실제 판정과 같다."""
        self.spawn(DIE)
        time.sleep(0.6)
        status, _, proc = registry.restart_if_dead("probe", by="launcher")
        self.assertEqual(status, registry.RESTARTED)
        if proc:
            self.started.append(proc)
            proc.wait(timeout=10)

        e = self.entry()
        self.assertEqual(len(e["restarts"]), 1)
        self.assertEqual(e["stop_reason"], None, "되살아났으면 멈춘 게 아니다")
        # 두 번째 재시작은 BACKOFF_S[1] = 2초 뒤부터다.
        self.assertAlmostEqual(e["next_restart_at"], e["restarts"][-1] + registry.BACKOFF_S[1],
                               delta=0.01)
        self.assertGreater(e["next_restart_at"], time.time())

        # 실제로 지금 물어보면 그 시각 때문에 BACKOFF 가 나온다.
        self.assertEqual(registry.restart_if_dead("probe", by="watchdog")[0], registry.BACKOFF)

    def test_backoff_wait_does_not_rewrite_the_file(self):
        self.spawn(DIE)
        time.sleep(0.6)
        _, _, proc = registry.restart_if_dead("probe", by="launcher")
        if proc:
            self.started.append(proc)
            proc.wait(timeout=10)
        before = os.stat(registry.PID_FILE).st_mtime_ns
        for _ in range(5):
            self.assertEqual(registry.restart_if_dead("probe", by="watchdog")[0],
                             registry.BACKOFF)
        self.assertEqual(os.stat(registry.PID_FILE).st_mtime_ns, before)

    def test_giving_up_clears_the_waiting_time(self):
        """더 안 되살리는데 '언제부터' 가 남아 있으면 되살아날 것으로 읽힌다."""
        self.spawn(DIE)
        time.sleep(0.5)
        now = time.time()
        with registry.edit() as d:
            # 한도만큼 이미 되살린 것으로 둔다. 실제로 5번 돌리면 간격 때문에 오래 걸린다.
            d["entries"]["probe"]["restarts"] = [now - 1] * registry.MAX_RESTARTS
            d["entries"]["probe"]["next_restart_at"] = now + 999

        self.assertEqual(registry.restart_if_dead("probe", by="launcher")[0], registry.GAVE_UP)
        e = self.entry()
        self.assertTrue(e["gave_up"])
        self.assertEqual(e["stop_reason"], "gave_up")
        self.assertEqual(e["next_restart_at"], 0.0)

    def test_ready_at_agrees_with_the_backoff_table(self):
        now = 1000.0
        self.assertEqual(registry.ready_at([], now), 0.0, "기록이 없으면 지금 바로")
        for n in range(1, len(registry.BACKOFF_S) + 2):
            stamps = [now - 0.1] * n
            want = stamps[-1] + registry.BACKOFF_S[min(n, len(registry.BACKOFF_S) - 1)]
            self.assertAlmostEqual(registry.ready_at(stamps, now), want, delta=1e-9)

    def test_stale_restarts_outside_the_window_do_not_hold_it_back(self):
        now = time.time()
        self.assertEqual(registry.ready_at([now - registry.WINDOW_S - 1], now), 0.0)


class RealReaderCompatTests(_RegistryTempDir):
    """4번 AntiDebug 의 **실제 읽기 코드**로 읽어 본다.

    규칙을 여기에 베껴 쓰면 저쪽이 바뀌었을 때 이 시험은 통과한 채로 운영이 깨진다.
    그래서 client/SelfDefense/anti_debug/registry_reader.py 를 그대로 불러 쓴다.
    """

    def setUp(self):
        super().setUp()
        ad = LAUNCHER_DIR.parent / "SelfDefense" / "anti_debug"
        if not (ad / "registry_reader.py").is_file():
            self.skipTest("anti_debug 가 아직 레포에 없습니다")
        if str(ad) not in sys.path:
            sys.path.insert(0, str(ad))
        import registry_reader            # noqa: E402  (PR #124 그대로)
        self.reader = registry_reader

    def test_anti_debug_reads_our_entries(self):
        proc = self.spawn()
        registry.note_exit("probe", code=2, reason=registry.STOP_CRASH_LIMIT)
        snap = self.reader.RegistryReader(Path(registry.PID_FILE),
                                          "stop_reason_test").load()
        self.assertFalse(snap.stopping)
        self.assertEqual(snap.owner.pid, os.getpid())
        self.assertEqual({t.name: t.pid for t in snap.targets}, {"probe": proc.pid})

    def test_anti_debug_rejects_a_wrong_session(self):
        # 우리 파일이 그쪽 검사를 실제로 통과한 것이지, 그쪽이 아무거나 받는 게 아니다.
        self.spawn()
        with self.assertRaises(self.reader.RegistryError):
            self.reader.RegistryReader(Path(registry.PID_FILE), "다른세션").load()


class StrictShapeTests(_RegistryTempDir):
    """읽는 쪽이 요구하는 값의 모양. 더한 칸이 그 모양을 깨지 않는지."""

    def test_entry_still_parses_under_strict_reading(self):
        self.spawn()
        registry.note_exit("probe", code=2, reason=registry.STOP_CRASH_LIMIT)
        d = json.loads(Path(registry.PID_FILE).read_text(encoding="utf-8"))

        self.assertIs(type(d["stopping"]), bool)
        self.assertIs(type(d["entries"]), dict)
        self.assertEqual(d["session_id"], "stop_reason_test")
        self.assertIs(type(d["launcher_pid"]), int)
        self.assertIs(type(d["launcher_create_time"]), int)
        for name, e in d["entries"].items():
            self.assertRegex(name, r"^[A-Za-z0-9_.-]{1,128}$")
            self.assertIs(type(e), dict)
            self.assertIs(type(e["pid"]), int)
            self.assertIs(type(e["create_time"]), int)
            self.assertGreater(e["pid"], 0)

    def test_added_fields_are_plain_json_types(self):
        self.spawn()
        registry.note_exit("probe", code=2, reason=registry.STOP_EXITED)
        e = json.loads(Path(registry.PID_FILE).read_text(encoding="utf-8"))["entries"]["probe"]
        self.assertIs(type(e["stop_reason"]), str)
        self.assertIn(type(e["next_restart_at"]), (int, float))
        self.assertIs(type(e["last_exit"]["code"]), int)
        self.assertIn(type(e["last_exit"]["at"]), (int, float))


class ProcessManagerIntegrationTests(unittest.TestCase):
    """런처를 실제로 돌려 본다. 어느 경로가 어떤 사유를 적는지가 이 시험의 요점이다."""

    def test_oneshot_finish_and_shutdown_reasons(self):
        # end_session 은 세션이 끝나면 entries 를 통째로 비운다(의도된 동작 — 끝난
        # 세션의 모듈이 살아 있는 것처럼 보이면 안 된다). 그래서 종료 중 사유는
        # 비우기 **직전**에 본다. 같이 돌고 있는 읽는 쪽이 보는 것이 그 상태다.
        script = (
            "import json, os, sys, time\n"
            "sys.path.insert(0, %r)\n"
            "import modules, process_manager, registry\n"
            "one = modules.Module(name='once', owner='t',\n"
            "    argv=[sys.executable, '-c', 'raise SystemExit(1)'],\n"
            "    mode=modules.ONESHOT, every_s=3600.0)\n"
            "stay = modules.Module(name='stay', owner='t',\n"
            "    argv=[sys.executable, '-c', 'import time; time.sleep(60)'],\n"
            "    mode=modules.CONTINUOUS, restart=False)\n"
            "pm = process_manager.ProcessManager([one, stay], 'pm_test', 'p', 0.0,\n"
            "                                    say=lambda *a, **k: None)\n"
            "pm.start('once'); pm.start('stay')\n"
            "for _ in range(200):\n"
            "    pm.poll()\n"
            "    if (registry.entry('once') or {}).get('stop_reason'): break\n"
            "    time.sleep(0.05)\n"
            "out = {'once_running_session': dict(registry.entry('once') or {})}\n"
            "orig = registry.end_session\n"
            "def spy():\n"
            "    out['at_session_end'] = {k: dict(v) for k, v in\n"
            "                             (registry.load().get('entries') or {}).items()}\n"
            "    orig()\n"
            "registry.end_session = spy\n"
            "pm.stop_all()\n"
            "out['after_session'] = registry.load().get('entries')\n"
            "print('@@' + json.dumps(out))\n"
        ) % str(LAUNCHER_DIR)

        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, AC_LAUNCHER_LOG_DIR=tmp, PYTHONIOENCODING="utf-8")
            run = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace",
                                 env=env, timeout=300, cwd=str(LAUNCHER_DIR.parents[1]))
        self.assertIn("@@", run.stdout, f"{run.stdout}\n{run.stderr}")
        got = json.loads(run.stdout.split("@@", 1)[1].splitlines()[0])

        # 세션이 도는 동안: 주기 검사가 한 바퀴 끝난 것은 '멈춤' 이 아니라 '끝남' 이다.
        once = got["once_running_session"]
        self.assertEqual(once["stop_reason"], "finished")
        self.assertEqual(once["last_exit"]["code"], 1)
        self.assertEqual(once["next_restart_at"], 0.0, "되살릴 일이 없다")

        # 종료 중: 런처가 껐다. 스스로 죽은 것과 구분된다.
        self.assertEqual(got["at_session_end"]["stay"]["stop_reason"], "stopped_by_launcher")

        # 세션이 끝난 뒤: 항목 자체가 없다. 끝난 세션을 '멈춘 모듈' 로 남기지 않는다.
        self.assertEqual(got["after_session"], {})


if __name__ == "__main__":
    unittest.main()
