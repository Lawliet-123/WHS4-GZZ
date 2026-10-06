"""injection 부분 검사 coverage 회귀 테스트.

.text baseline이 없으면 해당 비교는 수행되지 않는다.
이 상태를 완전한 정상 검사로 표현하면 안 되며,
coverage_complete=False가 중앙 전송 evidence까지 전달되어야 한다.
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
MI = os.path.dirname(HERE)
sys.path.insert(0, MI)

from core.result import DetectorResult, to_team_event, to_shared_event

fails = []


def check(condition, message):
    print(("  [통과] " if condition else "  [실패] ") + message)
    if not condition:
        fails.append(message)


print("1) 부분 검사는 coverage_complete=False를 가져야 한다")
res = DetectorResult("injection")
res.meta["coverage_complete"] = False
res.meta["skipped_checks"] = [".text_hash"]
res.meta["checks_run"] = 4

check(res.meta.get("coverage_complete") is False,
      "DetectorResult에 부분 검사 상태가 남는다")

print("\n2) 중앙 전송 evidence에서 coverage_complete를 직접 읽을 수 있어야 한다")
team = to_team_event(res, "partial_coverage_test", player_id="player_test")
shared = to_shared_event(team)

check(shared["evidence"].get("coverage_complete") is False,
      "shared evidence.coverage_complete == False")

print("\n3) skipped_checks도 운영 분석에서 확인 가능해야 한다")
check(".text_hash" in shared["evidence"].get("skipped_checks", []),
      "shared evidence.skipped_checks에 .text_hash가 남는다")

print("\n" + ("전체 통과" if not fails
              else f"실패 {len(fails)}건: " + "; ".join(fails)))

print("\n4) ?? injection.scan(None)? ?? coverage? ?? ???? ??")

from detectors import injection as I
from core import scan_engine as D


class FakeBase:
    lpBaseOfDll = 0x140000000


class FakeModule:
    def __init__(self, name, path, base, size):
        self.name = name
        self.filename = path
        self.lpBaseOfDll = base
        self.SizeOfImage = size


class FakePM:
    process_id = 4242
    process_base = FakeBase()

    def list_modules(self):
        return [
            FakeModule(
                D.GAME_EXE,
                r"C:\Game\MECCHA CHAMELEON\Chameleon\Binaries\Win64\PenguinHotel-Win64-Shipping.exe",
                0x140000000,
                0x100000,
            )
        ]


saved = {
    "open_game": I.procopen.open_game,
    "scan_objects": D.scan_objects,
    "module_rows": D._module_rows,
    "module_names": D.check_module_names,
    "module_trust": D.check_module_trust,
    "text_hash": D.check_text_hash,
    "vtable": D.check_vtable,
    "exec_function": D.check_exec_function,
    "trusted_modules": I.ue4ss_trust.trusted_modules,
    "ranges_of": I.ue4ss_trust.ranges_of,
    "attempted_env_override": I.ue4ss_trust.attempted_env_override,
}

try:
    pm = FakePM()

    I.procopen.open_game = lambda _exe: pm
    D.scan_objects = lambda _pm, _base: ([(0x1000, 0x2000, 0x3000)], 1)
    D._module_rows = lambda _pm: [
        (
            D.GAME_EXE,
            r"C:\Game\MECCHA CHAMELEON\Chameleon\Binaries\Win64\PenguinHotel-Win64-Shipping.exe",
        )
    ]

    clean = lambda name: D.Detection(name, False, "clean")
    D.check_module_names = lambda _pm: clean("module names")
    D.check_module_trust = lambda _pm, _game_dir: clean("module trust")
    D.check_text_hash = lambda _pm, _base, _baseline: clean("text hash")
    D.check_vtable = lambda _pm, _rows, _ranges, _trusted: clean("vtable")
    D.check_exec_function = lambda _pm, _rows, _ranges, _trusted: clean("exec")

    I.ue4ss_trust.trusted_modules = lambda _paths: (set(), "none")
    I.ue4ss_trust.ranges_of = lambda _names, _ranges: {}
    I.ue4ss_trust.attempted_env_override = lambda: None

    actual = I.scan(None)

    check(actual.meta.get("checks_run") == 4,
          f".text ?? ? ?? ?? ?? 4? ({actual.meta.get('checks_run')})")
    check(actual.meta.get("skipped_checks") == [".text_hash"],
          f".text_hash? skipped_checks? ???? ({actual.meta.get('skipped_checks')})")
    check(actual.meta.get("coverage_complete") is False,
          "baseline ?? injection? coverage_complete=False")
    check(actual.meta.get("measurement_valid") is True,
          "??? ??? ?????? measurement_valid=True")

    actual_shared = to_shared_event(
        to_team_event(actual, "detector_partial_test", player_id="player_test")
    )
    check(actual_shared["evidence"].get("coverage_complete") is False,
          "?? detector ??? partial coverage? ?? evidence?? ????")

finally:
    I.procopen.open_game = saved["open_game"]
    D.scan_objects = saved["scan_objects"]
    D._module_rows = saved["module_rows"]
    D.check_module_names = saved["module_names"]
    D.check_module_trust = saved["module_trust"]
    D.check_text_hash = saved["text_hash"]
    D.check_vtable = saved["vtable"]
    D.check_exec_function = saved["exec_function"]
    I.ue4ss_trust.trusted_modules = saved["trusted_modules"]
    I.ue4ss_trust.ranges_of = saved["ranges_of"]
    I.ue4ss_trust.attempted_env_override = saved["attempted_env_override"]

sys.exit(1 if fails else 0)
