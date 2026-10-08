"""시작할 때 '이 파이썬에 없는 패키지' 를 먼저 알려주는가.

2026-10-07 전체 E2E 에서 세 사람이 각각 다른 모듈에서 막혔는데 뿌리가 같았다.
런처를 띄운 파이썬에 패키지가 없었던 것이다(찬준님 pymem, 동효님 yara). 실패가
모듈 안에서 "검사 실패" 로만 나와서 각자 로그를 파헤친 뒤에야 알았다.

표가 실제 코드와 어긋나면 이 장치는 조용히 쓸모없어진다. 그래서 표를 믿지 않고
**모듈 폴더의 import 를 실제로 훑어서** 양쪽으로 맞춰 본다.
"""
import ast
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

LAUNCHER_DIR = Path(__file__).resolve().parents[1]
REPO = LAUNCHER_DIR.parents[1]
if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import main  # noqa: E402
import modules  # noqa: E402
import process_manager  # noqa: E402

# 이것만 본다. 우리 레포 안의 이름까지 외부 패키지로 세면 거짓 경보가 난다.
WATCHED = {"pymem", "yara", "psutil", "PyQt5",
           "win32api", "win32con", "win32evtlog", "win32job", "win32service"}
# scripts: 런처가 부르지 않는 보조 도구다. 여기 import 를 세면 거짓 경고가 된다.
SKIP_DIRS = {"__pycache__", "logs", "runs", "tests", ".git", "node_modules", "scripts"}

# 훑기로는 못 가리는 자리. **빼는 이유를 여기 적어 둔다** — 비워 두면 다음 사람이
# 왜 빠졌는지 모른 채 표에 도로 넣는다.
NOT_ON_LAUNCHER_PATH = {
    # 런처는 run.py 를 --headless 로만 띄운다(modules.py). PyQt5 는 run_gui()
    # 안에서만 import 되어 그 줄에 닿지 않는다. 실제로 막고 돌려도 정상 종료한다.
    ("esp", "PyQt5"),
    # approved_access.py 의 `if command:` 안쪽 지연 import 인데, 런처 경로는
    # command=False 로 부른다. 없어도 접근 면제만 못 해 주고 계속 돈다.
    ("kernel_watcher", "psutil"),
}


def _guarded_names(tree):
    """try/except ImportError 로 감싼 import 이름들.

    없으면 모듈이 스스로 저하 경로를 타므로 '필수' 가 아니다. 이걸 안 가리면
    esp 의 win32evtlog 처럼 소유자가 일부러 선택사항으로 둔 것까지 필수로 센다.
    """
    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches_import = any(
            h.type is None
            or (isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "ModuleNotFoundError"))
            or (isinstance(h.type, ast.Tuple) and any(
                isinstance(e, ast.Name) and e.id in ("ImportError", "ModuleNotFoundError")
                for e in h.type.elts))
            for h in node.handlers)
        if not catches_import:
            continue
        for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if isinstance(inner, ast.Import):
                guarded.update(a.name.split(".")[0] for a in inner.names)
            elif isinstance(inner, ast.ImportFrom) and inner.level == 0 and inner.module:
                guarded.add(inner.module.split(".")[0])
    return guarded


def imports_under(path: Path) -> set:
    """감싸지지 않은 import 만. 함수 안 import 는 뺀다고 맞지 않는다 —
    yara 는 load_rules() 안이지만 실행 경로가 반드시 닿는 진짜 의존이다."""
    found = set()
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(".py"):
                continue
            try:
                tree = ast.parse((Path(root) / name).read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeError):
                continue
            guarded = _guarded_names(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        found.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
            found -= guarded
    return found


def module_dir(name: str):
    m = modules.by_name()[name]
    script = m.script_path()
    base = Path(script).parent if script else Path(m.cwd or REPO)
    return base if base.is_dir() else None


class RequirementTableMatchesCodeTests(unittest.TestCase):
    """표가 실제 import 와 맞는가. 한쪽으로만 보면 표가 낡아도 안 걸린다."""

    @classmethod
    def setUpClass(cls):
        cls.scanned = {}
        for name in modules.by_name():
            base = module_dir(name)
            cls.scanned[name] = imports_under(base) & WATCHED if base else set()

    def test_every_listed_package_is_really_imported(self):
        for name, reqs in main.MODULE_REQUIREMENTS.items():
            self.assertIn(name, modules.by_name(), f"{name} 은 등록된 모듈이 아니다")
            for import_name, _pip in reqs:
                with self.subTest(module=name, package=import_name):
                    self.assertIn(import_name, self.scanned[name],
                                  f"{name} 이 {import_name} 을 더는 안 쓴다 — 표에서 빼라")

    def test_every_imported_package_is_listed(self):
        for name, used in self.scanned.items():
            listed = {i for i, _ in main.MODULE_REQUIREMENTS.get(name, ())}
            for import_name in used:
                with self.subTest(module=name, package=import_name):
                    if (name, import_name) in NOT_ON_LAUNCHER_PATH:
                        continue          # 이유는 그 집합 옆에 적어 두었다
                    self.assertIn(import_name, listed,
                                  f"{name} 이 {import_name} 을 쓰는데 표에 없다 — "
                                  "런처가 타는 경로면 표에 넣고, 아니면 "
                                  "NOT_ON_LAUNCHER_PATH 에 이유와 함께 적어라")

    def test_exclusions_are_still_needed(self):
        """빼 둔 항목이 실제로 아직 import 되는가. 코드가 바뀌면 같이 치운다."""
        for module_name, import_name in NOT_ON_LAUNCHER_PATH:
            with self.subTest(module=module_name, package=import_name):
                self.assertIn(import_name, self.scanned.get(module_name, set()),
                              f"{module_name} 이 {import_name} 을 더는 안 쓴다 — "
                              "NOT_ON_LAUNCHER_PATH 에서 빼라")

    def test_guarded_imports_are_not_counted(self):
        """try/except ImportError 로 감싼 import 는 필수가 아니다."""
        tree = ast.parse("try:\n import win32evtlog\nexcept ImportError:\n win32evtlog = None\n")
        self.assertEqual(_guarded_names(tree), {"win32evtlog"})
        plain = ast.parse("import pymem\n")
        self.assertEqual(_guarded_names(plain), set())

    def test_pip_names_are_plausible(self):
        pairs = {(i, p) for reqs in main.MODULE_REQUIREMENTS.values() for i, p in reqs}
        self.assertIn(("yara", "yara-python"), pairs, "import 이름과 pip 이름이 다른 대표 사례")
        for _import_name, pip_name in pairs:
            self.assertRegex(pip_name, r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    def test_soft_dependencies_are_not_announced_as_failures(self):
        """없어도 모듈이 도는 것은 표에 넣지 않는다.

        런처는 esp 를 --headless 로만 띄워서 PyQt5 에 닿지 않고, pywin32·psutil 은
        없으면 수집 범위만 준다. 이걸 "검사 실패로 끝납니다" 와 같이 알리면 경고
        전체가 무시당한다 — 10/7 밤을 날린 게 그 무시였다.
        """
        listed = {p for reqs in main.MODULE_REQUIREMENTS.values() for _i, p in reqs}
        for soft in ("PyQt5", "pywin32", "psutil"):
            self.assertNotIn(soft, listed,
                             f"{soft} 는 없어도 모듈이 돈다. 표에 넣으면 거짓 경고가 된다")


class _State:
    # **기본값이 PENDING 인 이유가 있다.** preflight() 는 ProcessManager 를 만든
    # 직후, 아무 모듈도 start 되기 전에 돈다(main.py). 그때 모든 모듈의 상태는
    # PENDING 이다. 시험이 RUNNING 만 쓰면, 필터를 `!= RUNNING` 으로 바꿔서
    # **실제 런처에서 경고가 한 번도 안 뜨게 만드는 변이**가 그대로 통과한다.
    def __init__(self, name, status=process_manager.PENDING):
        self.name = name
        self.status = status


class MissingRequirementsTests(unittest.TestCase):

    def setUp(self):
        # 설치 여부를 실제 환경에 맡기면 PC 마다 결과가 달라진다.
        self.absent = set()
        p = mock.patch.object(main, "_installed", lambda n: n not in self.absent)
        p.start()
        self.addCleanup(p.stop)

    def test_reports_only_what_is_absent(self):
        self.absent = {"pymem"}
        # 아직 안 뜬 모듈(preflight 가 실제로 보는 상태)과 이미 뜬 모듈 둘 다
        # 경고 대상이다. 상태 필터가 좁아지면 여기서 걸린다.
        for status in (process_manager.PENDING, process_manager.RUNNING):
            with self.subTest(status=status):
                got = main.missing_requirements(
                    [_State("memory_integrity", status), _State("noclip", status)])
                self.assertEqual(got, {"pymem": ["memory_integrity"]})

    def test_warns_for_modules_that_have_not_started_yet(self):
        """preflight 는 start 전에 돈다. 그 시점 상태에서 경고가 나와야 한다."""
        self.absent = {"pymem"}
        pending = _State("memory_integrity", process_manager.PENDING)
        self.assertEqual(main.missing_requirements([pending]),
                         {"pymem": ["memory_integrity"]})

    def test_groups_modules_under_one_package(self):
        self.absent = {"pymem"}
        got = main.missing_requirements(
            [_State("memory_integrity"), _State("whistle_spoofing")])
        self.assertEqual(sorted(got["pymem"]), ["memory_integrity", "whistle_spoofing"])

    def test_nothing_absent_means_no_warning(self):
        self.assertEqual(main.missing_requirements([_State("memory_integrity")]), {})

    def test_unselected_modules_are_not_nagged_about(self):
        # --only 로 하나만 돌릴 때 남의 패키지까지 깔라고 하면 경고가 무시당한다.
        self.absent = {"yara"}
        self.assertEqual(main.missing_requirements([_State("memory_integrity")]), {})

    def test_modules_that_will_not_run_are_skipped(self):
        self.absent = {"pymem"}
        for status in (process_manager.MISSING, process_manager.SKIPPED):
            with self.subTest(status=status):
                self.assertEqual(
                    main.missing_requirements([_State("memory_integrity", status)]), {})

    def test_modules_without_requirements_are_fine(self):
        self.absent = {"pymem", "yara"}
        self.assertEqual(main.missing_requirements([_State("noclip")]), {})


class PreflightOutputTests(unittest.TestCase):

    def _preflight_lines(self, absent):
        lines = []
        pm = mock.Mock()
        pm.states = {"memory_integrity": _State("memory_integrity")}
        with mock.patch.object(main, "_installed", lambda n: n not in absent), \
                mock.patch.object(main.ui, "line", lines.append), \
                mock.patch.object(main, "is_admin", return_value=True), \
                mock.patch.object(main, "publish_game_dir", return_value=None):
            main.preflight(pm, None)
        return "\n".join(lines)

    def test_warning_names_the_package_module_and_this_python(self):
        text = self._preflight_lines({"pymem"})
        self.assertIn("pymem", text)
        self.assertIn("memory_integrity", text)
        # 어젯밤 혼선의 핵심이 "어느 파이썬에 깔아야 하나" 였다.
        self.assertIn(sys.executable, text)
        self.assertIn("pip install", text)

    def test_quiet_when_everything_is_installed(self):
        text = self._preflight_lines(set())
        self.assertNotIn("pip install", text)


class RequirementsFileTests(unittest.TestCase):

    @staticmethod
    def _declared(path):
        """주석을 뺀 '실제로 설치되는' 줄만. 부분문자열로 보면 줄을 지워도
        설명 주석에 이름이 남아 있어 통과한다."""
        names = []
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                names.append(re.split(r"[<>=!~\[;]", line, 1)[0].strip())
        return names

    def test_file_exists_and_really_installs_the_table(self):
        path = LAUNCHER_DIR / "requirements.txt"
        self.assertTrue(path.is_file(), "런처용 requirements.txt 가 있어야 한다")
        declared = self._declared(path)
        for reqs in main.MODULE_REQUIREMENTS.values():
            for _import_name, pip_name in reqs:
                self.assertIn(pip_name, declared,
                              f"{pip_name} 이 requirements.txt 에서 실제로 설치되지 않는다")

    def test_starts_with_a_utf8_bom(self):
        """한글 주석이 있으므로 BOM 이 반드시 필요하다.

        pip 의 auto_decode 는 BOM 이 없으면 로케일 인코딩으로 떨어진다. 한국어
        윈도(cp949)에서는 그 순간 UnicodeDecodeError 로 **설치 자체가** 깨진다.
        실제로 BOM 없이 썼다가 venv 의 pip 에서 재현했다. 이 파일의 존재 이유가
        "이대로 깔면 된다" 이므로, 깔리지 않으면 아무 의미가 없다.
        """
        raw = (LAUNCHER_DIR / "requirements.txt").read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"),
                        "UTF-8 BOM 으로 저장해야 한다 (encoding='utf-8-sig')")

    def test_pip_can_parse_it(self):
        # 인라인 주석은 '#' 앞에 공백이 있어야 pip 이 주석으로 본다. 틀리면
        # 패키지 이름에 붙어 설치가 통째로 깨진다. 디코딩까지 여기서 같이 본다.
        path = LAUNCHER_DIR / "requirements.txt"
        try:
            from pip._internal.network.session import PipSession
            from pip._internal.req.req_file import parse_requirements
        except ImportError:
            self.skipTest("이 파이썬에서 pip 내부 모듈을 쓸 수 없습니다")
        got = [r.requirement for r in parse_requirements(str(path), PipSession())]
        self.assertEqual(got, self._declared(path))


if __name__ == "__main__":
    unittest.main()
