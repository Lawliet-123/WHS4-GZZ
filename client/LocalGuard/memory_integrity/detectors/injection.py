"""주입·후킹 범용 탐지 — 역할표 2번의 A축 (핵 무관)

`detector.py` 의 검사 5종을 팀 공통 계약으로 옮기는 어댑터다.
**`detector.py` 는 건드리지 않는다.** 그쪽은 1차 실측(docs/20)을 돌린 코드라
그대로 두고, 여기서 점수와 안정 코드만 입힌다.

## 왜 whistle_detector 와 따로 있나

`whistle_detector.py` 는 휘파람 관련 **함수·클래스만** 본다. 대신 FName 을
풀어 "어떤 함수가, 어떤 클래스에서" 까지 말한다 — 귀속이 강하다.

이 파일은 반대로 **전체 오브젝트를 훑고 귀속은 DLL 이름까지만** 한다.
그래서 핵 종류를 가리지 않는다. 내 담당 4종 중

    Hide Anywhere   내부 C++ (DLL 주입)  → 걸린다
    Auto Paint v1   DLL 주입 + Python    → 걸린다
    휘파람 조작      DLL 주입             → 걸린다 (whistle 쪽이 더 자세히)
    ESP             외부 pymem 읽기 전용  → 안 걸린다. 바꾸는 게 없다

**값을 안 건드려도 주입만 하면 걸린다**는 게 `value_tamper.py` 와의 차이다.
둘은 서로 다른 경로라 한쪽을 피해도 다른 쪽에 걸린다.

## 점수 배분

    process_event_hooked   70   vtable 슬롯이 게임 모듈 밖
    exec_function_hooked   70   UFunction::ExecFunction 이 게임 모듈 밖
    text_section_modified  60   .text 해시 불일치 (인라인 패치)
    untrusted_module       40   서명·경로가 신뢰 범위 밖인 모듈

후킹 두 개가 60(DETECTED) 을 넘는 이유는 실측에서 오탐이 0 이었기 때문이다.
서명 검사는 40 으로 둔다 — 정상 오버레이·드라이버가 섞일 여지가 있다.

**모듈 이름 화이트리스트는 점수에서 뺐다.** 1차 실측에서 오탐 115건을 냈고,
PC 마다 구성이 달라 목록을 손으로 관리하는 한 안 없어진다. 빼되 숨기지는
않고 `meta` 에 남긴다 — 왜 뺐는지가 측정 결과의 일부다.

## .text 해시의 한계 (실측)

vtable·ExecFunction 후킹은 **.text 를 한 바이트도 안 바꾼다.** 함수 포인터만
교체하기 때문이다. 실측에서 후킹 상태와 깨끗한 상태의 해시가 그대로 같았다.
그래서 해시 검사는 후킹 탐지의 보조가 아니라 **다른 기법(인라인 패치)을
잡는 별도 신호**다. 기준 해시를 인자로 주지 않으면 비교 자체를 못 하므로
그때는 검사하지 않은 것으로 처리한다.
"""

import json
import os
import sys
import time

import pymem
import pymem.exception

from core import scan_engine as D
# 이 파일을 직접 실행해도 core/ 를 찾게 한다.
# 팀원마다 실행 방식이 달라서 둘 다 되게 해둔다.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from core.result import DetectorResult, Evidence

# Detection.check 문자열 -> (안정 코드, 점수)
#
# detector.py 가 한글 제목을 쓰므로 여기서 집계용 코드로 바꾼다.
# 제목이 바뀌면 조용히 매칭이 끊기지 않도록 아래에서 검증한다.
CODES = {
    "vtable 무결성":        ("process_event_hooked", 70),
    "ExecFunction 무결성":  ("exec_function_hooked", 70),
    ".text 해시":           ("text_section_modified", 60),
    "모듈 서명 + 경로":      ("untrusted_module", 40),
}

# 점수에서 제외하되 근거로는 남기는 검사
ADVISORY = {"모듈 이름 화이트리스트"}


def scan(baseline=None):
    r = DetectorResult("injection")
    t0 = time.time()

    try:
        pm = pymem.Pymem(D.GAME_EXE)
    except pymem.exception.ProcessNotFound:
        return r.unavailable("게임이 실행 중이 아닙니다")
    except Exception as e:
        return r.fail(f"게임에 붙지 못했습니다: {e}")

    try:
        base = pm.process_base.lpBaseOfDll
        module_ranges = {}
        for mod in pm.list_modules():
            name = mod.name.decode("utf-8", "replace") \
                if isinstance(mod.name, bytes) else mod.name
            module_ranges[name.lower()] = (
                mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage)
    except Exception as e:
        return r.fail(f"모듈 목록을 읽지 못했습니다: {e}")

    rows, total = D.scan_objects(pm, base)
    if rows is None:
        # 오프셋 불일치를 CLEAN 으로 뭉개지 않는다.
        return r.fail("GObjects 를 읽지 못했습니다. 오프셋이 이 빌드와 안 맞습니다.")

    game_dir = None
    for name, path in D._module_rows(pm):
        if name.lower() == D.GAME_EXE.lower():
            game_dir = os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.dirname(path))))
            break

    r.meta["target_pid"] = pm.process_id
    r.meta["objects"] = len(rows)
    r.meta["objects_total"] = total
    r.meta["modules"] = len(module_ranges)

    # 검사 하나가 터져도 나머지는 살린다. 빠진 검사는 meta 에 남긴다.
    checks = [
        ("모듈 이름 화이트리스트", lambda: D.check_module_names(pm)),
        ("모듈 서명 + 경로",      lambda: D.check_module_trust(pm, game_dir)),
        (".text 해시",            lambda: D.check_text_hash(pm, base, baseline)),
        ("vtable 무결성",         lambda: D.check_vtable(pm, rows, module_ranges)),
        ("ExecFunction 무결성",   lambda: D.check_exec_function(pm, rows, module_ranges)),
    ]

    failed = []
    ran = 0
    for title, fn in checks:
        try:
            det = fn()
        except Exception as e:
            failed.append(f"{title}: {e}")
            continue
        ran += 1

        if title in ADVISORY:
            # 점수에 넣지 않는다. 다만 결과는 보인다.
            r.meta["advisory_" + "module_name_whitelist"] = det.detail
            continue

        if title == ".text 해시" and baseline is None:
            # 기준 해시가 없으면 비교를 한 것이 아니다.
            r.meta["text_hash"] = "기준 해시 미지정 — 비교 안 함"
            continue

        code, points = CODES[title]
        if det.caught:
            first = det.detail.strip().splitlines()
            r.add(code, points, f"{title}: {first[0]}",
                  [Evidence("address" if "0x" in ln else "module", ln.strip())
                   for ln in first[1:4]])

    if failed:
        r.meta["failed_checks"] = failed
    r.meta["checks_run"] = ran
    r.meta["elapsed_ms"] = int((time.time() - t0) * 1000)

    if ran == 0:
        return r.fail("검사를 하나도 수행하지 못했습니다:\n  " + "\n  ".join(failed))

    if not r.reasons:
        r.detail = (f"오브젝트 {len(rows):,} / 모듈 {len(module_ranges)} "
                    f"— 주입·후킹 흔적 없음")
    return r


def _selftest():
    """detector.py 의 검사 제목이 바뀌면 여기서 걸린다.

    제목 문자열로 매칭하므로, 저쪽에서 제목을 고치면 이 파일은 에러 없이
    **점수를 0 으로 내보낸다.** 그게 정확히 조용한 미탐지다.
    """
    import inspect
    src = inspect.getsource(D)
    for title in list(CODES) + list(ADVISORY):
        assert f'"{title}"' in src, (
            f"detector.py 에 '{title}' 검사 제목이 없습니다. "
            f"제목이 바뀌었다면 CODES 를 같이 고쳐야 합니다.")


_selftest()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    session = argv[0] if argv else "injection_001"
    baseline = argv[1] if len(argv) > 1 else None
    from result import to_team_event
    ev = to_team_event(scan(baseline), session)
    print(json.dumps(ev, ensure_ascii=False, indent=2))
    return {"NORMAL": 0, "ERROR": 2, "OFFLINE": 2}.get(ev["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
