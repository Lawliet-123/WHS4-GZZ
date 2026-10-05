"""우리 관측 후크를 핵으로 세던 오탐 (10/5 normal_whistle_rpc_002).

## 무엇이 틀렸나

휘파람 관측용 `ac_whistle` DLL 을 주입하면, 그 DLL 은 도발 RPC 를 보려고
`UFunction::ExecFunction` 과 캐릭터 vtable 의 `ProcessEvent` 를 바꾼다.
멘토 검수에서 "안티치트도 후킹을 써도 된다" 는 확인을 받고 만든 것이다.

그런데 정상 세션(핵 없음, 후크만 주입)에서 이렇게 나왔다.

    injection  exec_function_hooked    70점 DETECTED  ×6바퀴
    whistle    character_vtable_hooked 60점 DETECTED  ×2바퀴

둘 다 **옆 경로에는 이미 있던 제외 검사가 빠져 있었다.**

    core/scan_engine.py  check_vtable        -> _is_self_addr 있음
                         check_exec_function -> **없었다**
    whistle.py           W-1 ExecFunction    -> rt.is_self_module 있음
                         W-2 캐릭터 vtable   -> **없었다**

휘파람 핵도 같은 두 자리를 바꾼다. 우리 후크가 상시 양수를 내면 **핵이 있는지
없는지 구분할 수 없다.** UE4SS 때(#108)와 똑같은 성질의 문제다.

## 여기서 확인하는 것

게임은 필요 없다. 가짜 프로세스로 주소 범위만 만들어 판정 경로를 돌린다.

    python client\\LocalGuard\\memory_integrity\\tests\\t_self_hook_exempt.py
"""

import importlib.util
import os
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
MI = os.path.dirname(HERE)
sys.path.insert(0, MI)

from core import scan_engine as D          # noqa: E402
from core import selfid                    # noqa: E402

fails = []


def check(c, msg):
    print(("  [통과] " if c else "  [실패] ") + msg)
    if not c:
        fails.append(msg)


# ── 가짜 프로세스 ────────────────────────────────────────────────────────
# 주소 배치는 10/5 실측과 같은 모양으로 둔다.
GAME_LO, GAME_HI = 0x00007FF700000000, 0x00007FF700F00000
SELF_LO, SELF_HI = 0x00007FFC00000000, 0x00007FFC00100000   # ac_whistle DLL
UE4SS_LO, UE4SS_HI = 0x00007FFD00000000, 0x00007FFD00200000  # ue4ss.dll
HACK_LO, HACK_HI = 0x00007FFE00000000, 0x00007FFE00100000    # 핵 DLL

# 우리 배포본 안의 경로여야 selfid 가 자기 것으로 본다.
SELF_DLL = os.path.join(selfid.SELF_ROOT, "detectors", "whistle-spoofing",
                        "native", "whistle_hook", "bin", "Release",
                        "ac_whistle_v9.dll")


class FakeMod:
    def __init__(self, name, path, lo, hi):
        self.name = name
        self.filename = path
        self.lpBaseOfDll = lo
        self.SizeOfImage = hi - lo


class FakePM:
    """vtable 슬롯 읽기만 지원하는 최소 가짜 프로세스."""

    def __init__(self, slots):
        self.slots = slots          # {주소: 8바이트로 읽힐 값}
        self.process_id = 4242

    def list_modules(self):
        return [
            FakeMod("PenguinHotel-Win64-Shipping.exe",
                    r"C:\Game\Chameleon\Binaries\Win64\PenguinHotel-Win64-Shipping.exe",
                    GAME_LO, GAME_HI),
            FakeMod("ac_whistle_v9.dll", SELF_DLL, SELF_LO, SELF_HI),
            FakeMod("ue4ss.dll", r"C:\Game\Chameleon\Binaries\Win64\ue4ss\UE4SS.dll",
                    UE4SS_LO, UE4SS_HI),
            FakeMod("evil.dll", r"C:\Temp\evil.dll", HACK_LO, HACK_HI),
        ]

    def read_bytes(self, addr, size):
        if addr in self.slots and size == 8:
            return struct.pack("<Q", self.slots[addr])
        raise OSError("못 읽음")


MODULE_RANGES = {
    "penguinhotel-win64-shipping.exe": (GAME_LO, GAME_HI),
    "ac_whistle_v9.dll": (SELF_LO, SELF_HI),
    "ue4ss.dll": (UE4SS_LO, UE4SS_HI),
    "evil.dll": (HACK_LO, HACK_HI),
}
TRUSTED = {"ue4ss.dll": (UE4SS_LO, UE4SS_HI)}

print("0) 전제 — selfid 가 ac_whistle 경로를 자기 것으로 본다")
check(selfid.is_self_path(SELF_DLL), f"우리 배포본 안의 DLL ({selfid.SELF_ROOT})")
check(not selfid.is_self_path(r"C:\Temp\evil.dll"), "바깥 DLL 은 자기 것이 아니다")

# ── check_exec_function ─────────────────────────────────────────────────
# rows = [(vtable, class_ptr, exec_fn)]. 한 클래스에 20개 이상 + 90% 이상이
# 게임 내부여야 UFunction 계열로 본다.
def exec_rows(outside_addr, n_out=1, n_in=40):
    cls = 0xC0FFEE
    rows = [(0, cls, GAME_LO + 0x1000 + i * 8) for i in range(n_in)]
    rows += [(0, cls, outside_addr) for _ in range(n_out)]
    return rows


print("\n1) ExecFunction — 우리 후크는 점수에서 빠지고 근거에는 남는다")
pm = FakePM({})
det = D.check_exec_function(pm, exec_rows(SELF_LO + 0x500), MODULE_RANGES, TRUSTED)
check(not det.caught, f"잡지 않는다 ({det.detail[:60]})")
check(det.note and "안티치트 자체" in det.note, f"근거에 남는다 ({det.note})")

print("2) ExecFunction — 남의 DLL 은 그대로 잡는다 (미탐지로 안 바뀐다)")
det = D.check_exec_function(pm, exec_rows(HACK_LO + 0x500), MODULE_RANGES, TRUSTED)
check(det.caught, "잡는다")
check("evil.dll" in det.detail, f"어느 모듈인지 말한다 ({det.detail[:70]})")

print("3) ExecFunction — 우리 후크와 핵이 같이 있으면 핵만 잡는다 (핵심)")
rows = exec_rows(HACK_LO + 0x500) + [(0, 0xC0FFEE, SELF_LO + 0x500)]
det = D.check_exec_function(pm, rows, MODULE_RANGES, TRUSTED)
check(det.caught and "evil.dll" in det.detail, "핵은 잡는다")
check("ac_whistle" not in det.detail, f"우리 것은 사유에 안 들어간다 ({det.detail[:70]})")
check(det.note and "ac_whistle" in det.note, "우리 것은 근거로만 남는다")

print("4) ExecFunction — UE4SS 제외도 그대로 동작한다 (#108 회귀)")
det = D.check_exec_function(pm, exec_rows(UE4SS_LO + 0x500), MODULE_RANGES, TRUSTED)
check(not det.caught, "해시가 맞는 UE4SS 는 안 잡는다")
check(det.note and "런처 설치 기록" in det.note, f"근거에 남는다 ({det.note})")
det = D.check_exec_function(pm, exec_rows(UE4SS_LO + 0x500), MODULE_RANGES, None)
check(det.caught, "등록부가 없으면 UE4SS 도 그대로 잡힌다")

# ── check_vtable ────────────────────────────────────────────────────────
VT = 0x1000
print("\n5) vtable — 우리 후크를 조용히 빼지 않는다")
slot = VT + D.PROCESS_EVENT_IDX * 8
pm = FakePM({slot: SELF_LO + 0x700})
det = D.check_vtable(pm, [(VT, 1, None)], MODULE_RANGES, TRUSTED)
check(not det.caught, "점수에는 안 들어간다")
check(det.note and "안티치트 자체" in det.note,
      f"근거에는 남는다 — 예전에는 조용히 건너뛰었다 ({det.note})")

print("6) vtable — 남의 DLL 은 잡는다")
pm = FakePM({slot: HACK_LO + 0x700})
det = D.check_vtable(pm, [(VT, 1, None)], MODULE_RANGES, TRUSTED)
check(det.caught and "evil.dll" in det.detail, "잡는다")

print("7) vtable — 게임 내부면 아무 일도 없다")
pm = FakePM({slot: GAME_LO + 0x700})
det = D.check_vtable(pm, [(VT, 1, None)], MODULE_RANGES, TRUSTED)
check(not det.caught and not det.note, "제외 기록도 없다")

print("\n8) 고치기 전이라면 1·3·5 가 전부 양수였다 (대조)")
# 제외를 끄면(자기 범위를 안 주면) 예전 동작이 된다.
saved = D._self_bases
D._self_bases = lambda pm: []
try:
    pm = FakePM({slot: SELF_LO + 0x700})
    before_vt = D.check_vtable(pm, [(VT, 1, None)], MODULE_RANGES, TRUSTED)
    before_ex = D.check_exec_function(FakePM({}), exec_rows(SELF_LO + 0x500),
                                      MODULE_RANGES, TRUSTED)
finally:
    D._self_bases = saved
check(before_ex.caught, "제외 전: ExecFunction 70점 경로가 떴다 (오탐 재현)")
check(before_vt.caught, "제외 전: vtable 도 떴다")

print("\n" + ("전부 통과" if not fails else f"실패 {len(fails)}건: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
