"""venv 파이썬으로 런처를 돌려도 등록부 pid 가 실제 검사 프로세스인지.

Windows venv 의 python.exe 는 진짜 파이썬을 한 번 더 띄우는 중간 실행기다. 그대로
띄우면 등록부에는 중간 실행기 pid 가 적히고 검사 코드는 그 자식에서 돈다. 등록부로
우리 프로세스를 알아보는 AntiDebug(#124)가 실제 검사 프로세스를 못 본다
(성민님 확인 요청, 10/6 실측: 등록 20268 / 실제 24388).
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
import venv
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

LAUNCHER_DIR = Path(__file__).resolve().parents[1]
if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import registry  # noqa: E402


class DirectPythonTests(unittest.TestCase):
    """중간 실행기를 건너뛰는 조건. 진짜 sys 는 건드리지 않고 registry 안의 sys 만 바꾼다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.base = self.root / "base" / "python.exe"
        self.base.parent.mkdir()
        self.base.write_bytes(b"")
        self.venv_py = str(self.root / "venv" / "Scripts" / "python.exe")

    def fake_sys(self, in_venv=True, base=None):
        return SimpleNamespace(
            executable=self.venv_py,
            _base_executable=str(self.base) if base is None else base,
            prefix=str(self.root / "venv") if in_venv else str(self.base.parent),
            base_prefix=str(self.base.parent),
        )

    def run_direct(self, argv, **kw):
        env = {}
        with mock.patch.object(registry, "sys", self.fake_sys(**kw)):
            return registry._direct_python(argv, env), env

    def test_venv_python_runs_base_python_with_venv_marker(self):
        argv = [self.venv_py, "client/x/main.py", "--session-id", "s1"]
        out, env = self.run_direct(argv)
        self.assertEqual(out, [str(self.base), "client/x/main.py", "--session-id", "s1"])
        self.assertEqual(env["__PYVENV_LAUNCHER__"], self.venv_py)
        self.assertEqual(argv[0], self.venv_py, "넘겨받은 argv 는 바꾸지 않는다")

    def test_path_spelling_does_not_matter(self):
        out, env = self.run_direct([self.venv_py.upper(), "-c", "pass"])
        self.assertEqual(out[0], str(self.base))
        self.assertIn("__PYVENV_LAUNCHER__", env)

    def test_outside_venv_is_unchanged(self):
        argv = [self.venv_py, "-c", "pass"]
        out, env = self.run_direct(argv, in_venv=False)
        self.assertIs(out, argv)
        self.assertEqual(env, {})

    def test_other_executables_are_unchanged(self):
        argv = [str(self.root / "Game.exe"), "-windowed"]
        out, env = self.run_direct(argv)
        self.assertIs(out, argv)
        self.assertEqual(env, {})

    def test_missing_base_python_keeps_current_behavior(self):
        argv = [self.venv_py, "-c", "pass"]
        for base in ("", str(self.root / "nowhere" / "python.exe"), self.venv_py):
            with self.subTest(base=base):
                out, env = self.run_direct(argv, base=base)
                self.assertIs(out, argv)
                self.assertEqual(env, {})

    def test_spawn_uses_direct_python_and_says_so_in_log(self):
        log = self.root / "logs" / "probe.log"
        with mock.patch.object(registry, "sys", self.fake_sys()), \
                mock.patch.object(registry.subprocess, "Popen") as popen:
            registry.spawn([self.venv_py, "-c", "pass"], str(self.root), str(log))
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [str(self.base), "-c", "pass"])
        self.assertEqual(kwargs["env"]["__PYVENV_LAUNCHER__"], self.venv_py)
        text = log.read_text(encoding="utf-8")
        self.assertIn("[venv]", text)
        self.assertIn(f"[cmd] {self.base} -c pass", text)


# 임시 venv 의 파이썬으로 돌린다. 런처가 venv 로 돌 때와 같은 상태다.
# 런처 경로(spawn → register)와 워치독 경로(restart_if_dead)를 둘 다 지난다.
_CHILD = textwrap.dedent(r"""
    import json, os, re, sys
    sys.path.insert(0, sys.argv[1])
    import registry

    PROBE = ("import os, sys; print('PROBE', os.getpid(), sys.prefix != sys.base_prefix, "
             "os.environ.get('__PYVENV_LAUNCHER__') is None, flush=True)")
    log = os.path.join(registry.LOG_DIR, "probe.log")
    argv = [sys.executable, "-c", PROBE]

    def probes():
        with open(log, encoding="utf-8") as f:
            return [m.groups() for m in re.finditer(r"PROBE (\d+) (\w+) (\w+)", f.read())]

    out = {"launcher_in_venv": sys.prefix != sys.base_prefix}
    registry.begin_session("venv_pid_test")
    try:
        proc = registry.spawn(argv, os.getcwd(), log)
        registry.register("probe", proc, by="launcher", restartable=True,
                          argv=argv, cwd=os.getcwd(), log=log)
        proc.wait(timeout=30)
        out["first"] = {"popen_pid": proc.pid, "entry_pid": registry.entry("probe")["pid"],
                        "entry_argv0": registry.entry("probe")["argv"][0],
                        "probe": probes()[-1]}
        status, pid, proc2 = registry.restart_if_dead("probe", by="watchdog")
        proc2.wait(timeout=30)
        out["restart"] = {"status": status, "popen_pid": pid,
                          "entry_pid": registry.entry("probe")["pid"], "probe": probes()[-1]}
        with open(log, encoding="utf-8") as f:
            out["venv_notes"] = f.read().count("[venv]")
    finally:
        registry.end_session()
    print(json.dumps(out))
""")


@unittest.skipUnless(os.name == "nt", "Windows venv 의 중간 실행기 문제다")
class RealVenvTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        # resolve: TEMP 가 8.3 짧은 이름(MALRAN~1)이면 venv 가 "위치가 바뀌었다" 경고를 낸다.
        root = Path(cls._tmp.name).resolve()
        venv.create(root / "venv", with_pip=False)
        py = root / "venv" / "Scripts" / "python.exe"
        env = dict(os.environ, AC_LAUNCHER_LOG_DIR=str(root / "logs"), PYTHONIOENCODING="utf-8")
        run = subprocess.run([str(py), "-c", _CHILD, str(LAUNCHER_DIR)], cwd=str(root),
                             capture_output=True, text=True, encoding="utf-8",
                             env=env, timeout=120)
        if run.returncode:
            raise AssertionError(f"venv 안 실행 실패:\n{run.stdout}\n{run.stderr}")
        cls.out = json.loads(run.stdout.strip().splitlines()[-1])
        cls.venv_py = str(py)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def check(self, part):
        got = self.out[part]
        probe_pid, in_venv, no_leak = got["probe"]
        self.assertEqual(int(probe_pid), got["popen_pid"], "Popen.pid 가 실제 검사 프로세스여야 한다")
        self.assertEqual(got["entry_pid"], got["popen_pid"], "등록부에 실제 검사 프로세스가 적혀야 한다")
        self.assertEqual(in_venv, "True", "venv 패키지를 그대로 써야 한다")
        self.assertEqual(no_leak, "True", "__PYVENV_LAUNCHER__ 가 자식 환경에 남으면 안 된다")

    def test_launcher_spawn_registers_worker_pid(self):
        self.assertTrue(self.out["launcher_in_venv"])
        self.check("first")
        self.assertEqual(os.path.normcase(self.out["first"]["entry_argv0"]),
                         os.path.normcase(self.venv_py), "등록부에는 원래 argv 를 적는다")

    def test_watchdog_restart_registers_worker_pid(self):
        self.assertEqual(self.out["restart"]["status"], registry.RESTARTED)
        self.check("restart")

    def test_log_marks_direct_start(self):
        self.assertEqual(self.out["venv_notes"], 2)


if __name__ == "__main__":
    unittest.main()
