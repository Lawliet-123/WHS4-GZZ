"""overlay_hook 판정 경로 검증용 하네스 (controlled_harness).

## 왜 필요한가

`overlay_hook` 의 **양수** 경로(60/100)는 실측된 적이 없다. 9/20 측정에서 찾은 인라인
후킹 573건이 전부 서명 유효(스팀 오버레이 등)라 정상 0점으로 끝났다. 중앙 서버 임계값을
정하려면 양수 Event 가 필요하다(10/4 은지님 B calibration 요청).

게임 안에서 그 조건을 만들려면 게임 프로세스에 후크를 심어야 한다. 그래서 **게임을 전혀
건드리지 않고** 탐지기의 판정 경로만 재는 방법을 쓴다.

## 무엇을 하는가

이 프로세스가 **자기 자신의** 메모리에서, 자기가 절대 호출하지 않는 익스포트 한 개의
진입부 바이트를 탐지기가 "모듈 밖으로 나가는 점프"로 읽을 모양으로 바꾼다. 끝날 때
원래 바이트로 되돌린다. 다른 프로세스는 열지도 않는다(자기 PID 를 읽기 전용으로만 연다).

**동작하는 후크가 아니다.** 트램폴린도 가로채기도 없다 — 그 함수를 실제로 부르면 그냥
죽는다. 탐지기가 바이트를 읽어 판정하는 경로만 재기 위한 바이트 패턴 표본이다.
그래서 이 결과는 **실제 게임 핵 표본이 아니고**, 탐지기가
`evidence.meta.measurement_scope = "controlled_harness"` 로 표시한다(10/5 은지님과 합의).

## 경우

    normal                  아무것도 안 바꿈                      0점
    general-unsigned        일반 익스포트 + 서명 없는 모듈        60
    general-unverifiable    일반 익스포트 + 서명 확인 불가        60
    render-unsigned         렌더링 익스포트 + 서명 없는 모듈      100 (60+60)
    render-unverifiable     렌더링 익스포트 + 서명 확인 불가      100

"패턴 제거 후 0점 복귀" 는 띄워 둔 채 `remove` 를 보내면 된다.
"재시작 후 0점" 은 끄고 `--case normal` 로 다시 띄우면 된다.

## 사용

    python client\\LocalGuard\\memory_integrity\\tests\\overlay_fixture.py --case render-unsigned
        -> READY {"pid": ..., "target_export": ..., ...} 를 찍고 기다린다

    탐지기를 이 PID 에 붙이는 것은 **인자로만** 된다. 환경변수 경로는 없앴다 —
    런처가 환경을 자식에게 물려줘서 운영 중 탐지 대상을 바꾸는 우회가 됐다
    (10/5 은지님 지적).

        python tests\\t_overlay_fixture.py          6가지 경우를 한 번에
        python tests\\t_overlay_fixture.py --central-url http://127.0.0.1:8002
                                                     중앙 전송까지 (서버가 떠 있을 때)

    픽스처 창에 `remove` + Enter  -> 원래 바이트로 되돌린다 (다시 재면 0점)
                `q` + Enter      -> 되돌리고 끝낸다

서명 없는 모듈이 자동으로 안 잡히면 `--unsigned-dll <경로>` 로 하나 지정한다.
`--case *-unverifiable` 는 그런 모듈이 없어도 그대로 쓸 수 있다.
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import time

# memory_integrity/tests/ 에 있다. core·detectors 는 그 상위에 있다.
MI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, MI)
from core import procopen                      # noqa: E402
from core.signature import verify              # noqa: E402
from detectors.overlay_hook import export_table, PROLOGUE  # noqa: E402

# 탐지기가 보는 대상 목록에서 고른다(detectors/overlay_hook.py TARGET_MODULES).
#   일반   — 이 프로세스가 네트워크를 안 쓰므로 ws2_32 익스포트는 호출되지 않는다
#   렌더링 — 파이썬은 dxgi 를 쓰지 않는다
GENERAL_DLL, RENDER_DLL = "ws2_32.dll", "dxgi.dll"
# 호출될 일이 없는 쪽을 먼저 고른다. 없으면 그 DLL 의 다른 익스포트를 쓴다.
PREFERRED = {
    GENERAL_DLL: ("WSAAsyncGetHostByName", "WSAAsyncGetServByName", "WSApSetPostRoutine"),
    RENDER_DLL: ("DXGIReportAdapterConfiguration", "DXGIDumpJournal", "CreateDXGIFactory2"),
}

PAGE_EXECUTE_READWRITE = 0x40
MEM_COMMIT_RESERVE = 0x1000 | 0x2000
PAGE_READWRITE = 0x04

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.VirtualProtect.argtypes = [wt.LPVOID, ctypes.c_size_t, wt.DWORD, ctypes.POINTER(wt.DWORD)]
k32.VirtualProtect.restype = wt.BOOL
k32.VirtualAlloc.argtypes = [wt.LPVOID, ctypes.c_size_t, wt.DWORD, wt.DWORD]
k32.VirtualAlloc.restype = wt.LPVOID


def modules_of_self():
    """자기 프로세스의 모듈 범위·경로. 탐지기와 같은 읽기 전용 권한으로 본다."""
    pm = procopen.open_pid(os.getpid())
    mods = {}
    for m in pm.list_modules():
        nm = m.name.decode("utf-8", "replace") if isinstance(m.name, bytes) else m.name
        p = m.filename.decode("utf-8", "replace") if isinstance(m.filename, bytes) else m.filename
        mods[nm.lower()] = (m.lpBaseOfDll, m.SizeOfImage, p)
    return pm, mods


def pick_export(pm, base, dll):
    """그 DLL 에서 바꿀 익스포트 하나. (이름, 주소)"""
    exports = dict(export_table(pm, base))
    for name in PREFERRED.get(dll, ()):
        if name in exports:
            return name, exports[name]
    if not exports:
        raise SystemExit(f"{dll} 익스포트를 읽지 못했습니다")
    name = sorted(exports)[0]
    return name, exports[name]


def unsigned_destination(mods, explicit=None):
    """서명 없는 모듈 안의 주소. (주소, 모듈명, 경로, 서명판정)

    이미 로드된 모듈 중에서 찾는다. PC 마다 다르므로 못 찾으면 --unsigned-dll 로 받는다.
    """
    if explicit:
        ctypes.WinDLL(explicit)              # 로드해서 모듈 목록에 올린다
        nm = os.path.basename(explicit).lower()
        _, mods = modules_of_self()
        if nm not in mods:
            raise SystemExit(f"{explicit} 를 로드했지만 모듈 목록에 없습니다")
        base, size, path = mods[nm]
        sig = verify(path)
        if sig == "유효":
            raise SystemExit(f"{nm} 는 서명이 유효해서 양수가 안 납니다 (판정: {sig})")
        return base + min(0x1000, size - 1), nm, path, sig

    for nm, (base, size, path) in sorted(mods.items()):
        if verify(path) == "서명없음":
            return base + min(0x1000, size - 1), nm, path, "서명없음"
    raise SystemExit(
        "이 프로세스에 서명 없는 모듈이 없습니다.\n"
        "  --unsigned-dll <경로> 로 서명 없는 DLL 을 하나 지정해 주세요.\n"
        "  (서명 확인 불가 경우는 --case *-unverifiable 로 그대로 쓸 수 있습니다)")


def unverifiable_destination():
    """모듈에 속하지 않는 메모리. 탐지기는 경로를 못 찾아 '확인불가' 로 본다.

    0 으로 채워 둔다 — 탐지기가 체인을 더 따라가지 않고 여기서 멈춘다.
    """
    addr = k32.VirtualAlloc(None, 0x1000, MEM_COMMIT_RESERVE, PAGE_READWRITE)
    if not addr:
        raise SystemExit(f"메모리를 못 잡았습니다 (오류 {ctypes.get_last_error()})")
    ctypes.memset(addr, 0, 0x1000)
    return addr, None, "", "확인불가"


def absolute_jump(dest):
    """탐지기가 읽는 네 가지 모양 중 **절대 주소** 형태.

    상대 점프는 ±2GB 안에만 닿는데 목적지가 멀 수 있어서 이 쪽을 쓴다.
    (detectors/overlay_hook.jump_target 의 `48 B8 imm64 ... FF E0` 분기)
    """
    return b"\x48\xB8" + struct.pack("<Q", dest) + b"\xFF\xE0"


class Patch:
    """자기 메모리의 익스포트 진입부 바이트를 바꾸고 되돌린다."""

    def __init__(self, addr, payload):
        self.addr = addr
        self.original = ctypes.string_at(addr, PROLOGUE)
        old = wt.DWORD()
        if not k32.VirtualProtect(ctypes.c_void_p(addr), PROLOGUE,
                                  PAGE_EXECUTE_READWRITE, ctypes.byref(old)):
            raise SystemExit(f"쓰기 권한을 못 얻었습니다 (오류 {ctypes.get_last_error()})")
        self.old_protect = old.value
        ctypes.memmove(addr, payload, len(payload))
        self.applied = True

    def remove(self):
        if not self.applied:
            return False
        ctypes.memmove(self.addr, self.original, len(self.original))
        old = wt.DWORD()
        k32.VirtualProtect(ctypes.c_void_p(self.addr), PROLOGUE,
                           self.old_protect, ctypes.byref(old))
        self.applied = False
        return True


CASES = {
    "normal": (None, None),
    "general-unsigned": (GENERAL_DLL, "unsigned"),
    "general-unverifiable": (GENERAL_DLL, "unverifiable"),
    "render-unsigned": (RENDER_DLL, "unsigned"),
    "render-unverifiable": (RENDER_DLL, "unverifiable"),
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="overlay_hook 판정 경로 검증용 하네스")
    ap.add_argument("--case", required=True, choices=sorted(CASES))
    ap.add_argument("--unsigned-dll", default=None,
                    help="서명 없는 모듈이 자동으로 안 잡힐 때 쓸 DLL 경로")
    ap.add_argument("--seconds", type=float, default=0,
                    help="이 시간이 지나면 되돌리고 끝낸다 (0 이면 입력을 기다린다)")
    a = ap.parse_args(argv)
    if os.name != "nt":
        raise SystemExit("윈도 전용입니다")

    dll, dest_kind = CASES[a.case]
    # 탐지기가 이 두 DLL 을 보도록 로드해 둔다. 정상 경우에도 로드해야
    # "검사했는데 후킹 없음(0점)" 이 같은 조건에서 나온다.
    for name in (GENERAL_DLL, RENDER_DLL):
        try:
            ctypes.WinDLL(name)
        except OSError:
            pass

    pm, mods = modules_of_self()
    info = {"pid": os.getpid(), "case": a.case, "scope": "controlled_harness",
            "note": "non-functional byte pattern for detector validation; not a game cheat sample"}
    patch = None

    if dll:
        if dll not in mods:
            raise SystemExit(f"{dll} 가 로드되지 않았습니다")
        base, size, path = mods[dll]
        name, addr = pick_export(pm, base, dll)
        if dest_kind == "unsigned":
            dest, dest_mod, dest_path, sig = unsigned_destination(mods, a.unsigned_dll)
        else:
            dest, dest_mod, dest_path, sig = unverifiable_destination()
        patch = Patch(addr, absolute_jump(dest))
        info.update({"target_dll": dll, "target_export": name,
                     "target_addr": hex(addr), "destination": hex(dest),
                     "destination_module": dest_mod, "destination_path": dest_path,
                     "destination_signature": sig,
                     "expected_raw_score": 100 if dll == RENDER_DLL else 60,
                     "original_bytes": patch.original.hex()})
    else:
        info.update({"expected_raw_score": 0})

    print("READY " + json.dumps(info, ensure_ascii=False), flush=True)
    print(f"  탐지기: overlay_hook.scan(target_pid={os.getpid()})  "
          f"(tests/t_overlay_fixture.py 가 이렇게 부른다)", flush=True)
    print("  remove + Enter = 패턴 제거(0점 복귀),  q + Enter = 끝내기", flush=True)

    try:
        if a.seconds:
            time.sleep(a.seconds)
        else:
            while True:
                line = sys.stdin.readline()
                if not line:
                    break
                cmd = line.strip().lower()
                if cmd in ("q", "quit", "exit"):
                    break
                if cmd == "remove":
                    print("REMOVED " + json.dumps(
                        {"removed": bool(patch and patch.remove()),
                         "expected_raw_score": 0}, ensure_ascii=False), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if patch and patch.applied:
            patch.remove()
        print("DONE 원래 바이트로 되돌렸습니다", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
