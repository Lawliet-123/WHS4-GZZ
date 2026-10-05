"""value_tamper — 생성 중인 객체를 변조로 세던 오탐 (10/5 normal_005).

정상 세션인데 한 바퀴가 통째로 100점 DETECTED 였다. 잡힌 값이 전부 비정규 실수였다.

    Angle=5.45353e-312 (기본값 20) / AngleBias=6.36599e-314 (기본값 2)
    InteractLength=8.18986e-312 (기본값 250) / IsInViewCheckLate=8.2058e-312

같은 바퀴에서 오브젝트가 57,795 -> 63,888 로 늘고 live_instances 가 2 -> 4 였다.
맵 로딩·스폰 도중에 초기화 전 메모리를 읽은 것이다. 핵이 바꾼 값이 아니다.

여기서는 그때 실제로 올라온 값을 그대로 넣어 판정 규칙을 검증한다.
게임은 필요 없다.

    python client\\LocalGuard\\memory_integrity\\tests\\t_value_tamper_uninit.py
"""

import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
MI = os.path.dirname(HERE)
sys.path.insert(0, MI)

_spec = importlib.util.spec_from_file_location(
    "value_tamper_under_test", os.path.join(MI, "detectors", "value_tamper.py"))
VT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(VT)

fails = []


def check(c, msg):
    print(("  [통과] " if c else "  [실패] ") + msg)
    if not c:
        fails.append(msg)


print("1) 초기화 전 메모리에서 읽힌 값은 설정값으로 안 본다")
# 10/5 normal_005 evidence 에 실제로 올라온 값들
REAL = [("Angle", 5.45353e-312), ("Angle", 8.18498e-312),
        ("AngleBias", 6.36599e-314), ("InteractLength", 8.18986e-312),
        ("IsInViewCheckLate", 8.2058e-312)]
for name, v in REAL:
    check(not VT._plausible(v, "f"), f"{name}={v:g} 는 쓸 수 없는 값")
check(not VT._plausible(float("nan"), "f") and not VT._plausible(float("inf"), "f"),
      "NaN·inf 도 쓸 수 없는 값")

print("2) 멀쩡한 설정값은 그대로 통과한다 (진짜 변조를 놓치면 안 된다)")
for v in (20.0, 2.0, 250.0, 0.01666, -1.5, 99999.0):
    check(VT._plausible(v, "f"), f"{v:g} 통과")
check(VT._plausible(0.0, "f"), "0 도 통과 — 핵이 0 으로 바꾸는 건 말이 되는 변조다")
check(VT._plausible(False, "b") and VT._plausible(0, "i") and VT._plausible(1 << 30, "i"),
      "bool·int 는 비정규 판정 대상이 아니다")

print("3) 대상 실수 필드가 전부 0 인 객체는 아직 안 채워진 것으로 본다")
allzero = [("Angle", "f", "x", 0.0, 20.0), ("AngleBias", "f", "x", 0.0, 2.0),
           ("EnableInteract", "b", "x", False, True)]
check(VT._uninitialized(allzero), "전부 0 + 기준은 0 아님 -> 초기화 전")
partial = [("Angle", "f", "x", 0.0, 20.0), ("AngleBias", "f", "x", 2.0, 2.0)]
check(not VT._uninitialized(partial), "일부만 0 이면 초기화 전이 아니다 (변조일 수 있다)")
base_zero = [("Angle", "f", "x", 0.0, 0.0)]
check(not VT._uninitialized(base_zero), "기준도 0 이면 초기화 전 판정 근거가 없다")
only_bool = [("EnableInteract", "b", "x", False, True)]
check(not VT._uninitialized(only_bool), "실수 필드가 없으면 이 규칙을 쓰지 않는다")
real_tamper = [("InteractLength", "f", "x", 99999.0, 250.0),
               ("Angle", "f", "x", 20.0, 20.0)]
check(not VT._uninitialized(real_tamper), "실제 변조(큰 값)는 안 걸러진다")

print("4) 고치기 전이라면 이 값들이 전부 '변조' 로 셌다 (대조)")
counted = sum(1 for _n, v in REAL if VT._differs(v, 20.0, "f"))
check(counted == len(REAL),
      f"_differs 만 보면 {counted}/{len(REAL)} 건이 기본값과 다르다 "
      f"— 그래서 _plausible 로 먼저 걸러야 한다")

print("\n" + ("전부 통과" if not fails else f"실패 {len(fails)}건: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
