"""인라인 후킹 · 오버레이 렌더링 후킹 탐지 — 역할표 2번의 나머지 두 칸

노션 역할표 2번은 네 가지다.

    기존 치트의 대상 값 감시          -> value_tamper.py
    코드 영역 무결성 검사             -> injection.py (.text 해시)
    주요 함수 인라인 후킹 검사        -> **이 파일**
    오버레이/렌더링 후킹 탐지         -> **이 파일**

둘을 한 파일에서 보는 이유는 **판정 방식이 같기 때문**이다. 렌더링 후킹도
결국 그래픽 API 함수 앞에 점프를 심는 인라인 후킹이다. 대상 모듈 목록만
다르다.

## 원리

인라인 후킹은 함수의 **첫 몇 바이트를 점프로 덮어쓴다.** x64 에서 쓰는
모양은 몇 가지로 정해져 있다.

    E9 xx xx xx xx                  상대 점프
    FF 25 xx xx xx xx               [rip+disp] 간접 점프
    48 B8 <주소 8바이트> FF E0      mov rax, imm64 ; jmp rax
    68 xx xx xx xx C3               push imm32 ; ret

그래서 **함수 진입부를 읽어 점프인지 보고, 점프면 어디로 가는지 계산한다.**
목적지가 그 함수를 소유한 모듈 밖이면 후킹이다. vtable 검사와 같은 논리 —
정상 값을 미리 저장할 필요가 없고 "안이냐 밖이냐"만 본다.

## 정상 오버레이를 오탐으로 내지 않기

**스팀 오버레이는 실제로 DXGI Present 를 후킹한다.** 이건 정상이다.
그래서 후킹 자체를 위반으로 보지 않고, **누가 후킹했는지**로 갈라낸다.
`signature.py` 의 Authenticode 검증을 재사용해서

    서명된 모듈이 후킹     -> 참고로만 남긴다 (점수 없음)
    서명 없는 모듈이 후킹  -> 의심 (60점)

1차 실측에서 모듈 이름 목록이 오탐 115건을 냈던 것과 같은 교훈이다.
이름이나 존재 여부가 아니라 **신뢰 근거**로 갈라야 한다.

## 한계

  - **vtable 방식 후킹은 못 잡는다.** COM 객체(IDXGISwapChain)의 vtable 을
    바꾸는 오버레이는 익스포트 함수를 안 건드린다. 스왑체인 포인터를 외부에서
    찾아야 하는데 안정적인 방법이 없어 이번 범위 밖이다.
  - **우리 팀 ESP 는 여기에도 안 걸린다.** 게임 안에 그리지 않고 별도 창에
    그려서 그래픽 API 를 아예 후킹하지 않는다. 실측으로 확인했다
    (MEASUREMENT.md). 노션 2번의 "ESP/월핵류 겨냥"은 *게임 안에 그리는*
    오버레이를 전제한 항목이다.
"""

import json
import struct
import sys
import time

import pymem
import pymem.exception

# 이 파일을 직접 실행해도 core/ 를 찾게 한다.
# 팀원마다 실행 방식이 달라서 둘 다 되게 해둔다.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from core.result import DetectorResult, Evidence
from core.signature import verify  # Authenticode 검증 재사용

GAME_EXE = "PenguinHotel-Win64-Shipping.exe"

# 렌더링 경로. 오버레이 후킹이 노리는 자리다.
RENDER_MODULES = ("dxgi.dll", "d3d11.dll", "d3d12.dll", "d3d9.dll",
                  "opengl32.dll", "vulkan-1.dll")

# 그 외 인라인 후킹이 자주 걸리는 자리.
#   입력   — 에임봇/매크로가 키·마우스를 가로채거나 위조한다
#   메모리 — 외부 접근을 숨기려고 후킹한다
#   네트워크 — 패킷 조작
CORE_MODULES = ("user32.dll", "kernel32.dll", "kernelbase.dll",
                "ntdll.dll", "ws2_32.dll")

TARGET_MODULES = RENDER_MODULES + CORE_MODULES

# 프롤로그를 볼 바이트 수. 위 점프 모양 중 제일 긴 것이 12바이트다.
PROLOGUE = 16


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def export_table(pm, base):
    """모듈의 익스포트 목록. [(이름, 함수주소)]

    전달 익스포트(forwarder)는 뺀다. 그건 코드가 아니라 문자열이라
    프롤로그를 읽으면 엉뚱한 바이트가 나온다.
    """
    hdr = pm.read_bytes(base, 0x400)
    if hdr[:2] != b"MZ":
        return []
    nt = base + _u32(hdr, 0x3C)
    opt = nt + 0x18
    magic = struct.unpack_from("<H", pm.read_bytes(opt, 2), 0)[0]
    dd = opt + (0x70 if magic == 0x20B else 0x60)      # DataDirectory
    d = pm.read_bytes(dd, 8)
    exp_rva, exp_size = _u32(d, 0), _u32(d, 4)
    if not exp_rva:
        return []

    e = pm.read_bytes(base + exp_rva, 0x28)
    n_names = _u32(e, 0x18)
    a_funcs = _u32(e, 0x1C)
    a_names = _u32(e, 0x20)
    a_ords = _u32(e, 0x24)
    if not n_names:
        return []

    names = pm.read_bytes(base + a_names, 4 * n_names)
    ords = pm.read_bytes(base + a_ords, 2 * n_names)
    out = []
    for i in range(n_names):
        try:
            name_rva = _u32(names, 4 * i)
            raw = pm.read_bytes(base + name_rva, 96)
            nm = raw.split(b"\0", 1)[0].decode("ascii", "replace")
            idx = struct.unpack_from("<H", ords, 2 * i)[0]
            fn_rva = _u32(pm.read_bytes(base + a_funcs + 4 * idx, 4), 0)
            # 익스포트 디렉터리 범위 안이면 forwarder 다.
            if exp_rva <= fn_rva < exp_rva + exp_size:
                continue
            out.append((nm, base + fn_rva))
        except Exception:
            continue
    return out


def jump_target(code, at):
    """프롤로그가 점프면 목적지를 돌려준다. 아니면 None."""
    if len(code) < 6:
        return None

    # E9 rel32
    if code[0] == 0xE9:
        rel = struct.unpack_from("<i", code, 1)[0]
        return at + 5 + rel

    # FF 25 disp32  ->  jmp [rip+disp]  (목적지 포인터는 따로 읽어야 한다)
    if code[0] == 0xFF and code[1] == 0x25:
        disp = struct.unpack_from("<i", code, 2)[0]
        return ("indirect", at + 6 + disp)

    # 48 B8 imm64 FF E0   mov rax, imm64 ; jmp rax
    if (len(code) >= 12 and code[0] == 0x48 and code[1] == 0xB8
            and code[10] == 0xFF and code[11] == 0xE0):
        return struct.unpack_from("<Q", code, 2)[0]

    # 68 imm32 C3   push imm32 ; ret   (32비트 주소라 x64 에선 드물다)
    if code[0] == 0x68 and len(code) >= 6 and code[5] == 0xC3:
        return _u32(code, 1)

    return None


def resolve_chain(pm, code, addr, owner, depth=4):
    """점프 체인을 끝까지 따라가 최종 목적지를 돌려준다.

    **한 단만 따라가면 범인을 놓친다.** 실측에서 스팀 오버레이가 2단
    트램폴린을 썼다 — 익스포트에 `E9 rel32` 를 심어 모듈 밖 스텁으로
    보내고, 그 스텁이 `FF 25 [rip+0]` + 절대주소로 실제 핸들러에 간다.
    1단만 보면 "알 수 없는 메모리"로 끝나서 누가 후킹했는지 알 수 없다.

    모듈 안에 도착하면 멈춘다. 그게 답이다.
    """
    tgt = jump_target(code, addr)
    for _ in range(depth):
        if tgt is None:
            return None
        if isinstance(tgt, tuple):          # jmp [rip+disp]
            try:
                tgt = struct.unpack("<Q", pm.read_bytes(tgt[1], 8))[0]
            except Exception:
                return None
        if owner(tgt):                      # 어느 모듈인지 알아냈다
            return tgt
        try:
            nxt = pm.read_bytes(tgt, PROLOGUE)
        except Exception:
            return tgt                      # 더 못 따라가면 여기까지
        step = jump_target(nxt, tgt)
        if step is None:
            return tgt
        tgt = step
    return tgt


def scan():
    r = DetectorResult("overlay_hook")
    t0 = time.time()

    try:
        pm = pymem.Pymem(GAME_EXE)
    except pymem.exception.ProcessNotFound:
        return r.unavailable("게임이 실행 중이 아닙니다")
    except Exception as e:
        return r.fail(f"게임에 붙지 못했습니다: {e}")

    # 모듈 범위표. 목적지가 어느 모듈인지 역추적하는 데 쓴다.
    ranges = {}
    paths = {}
    for m in pm.list_modules():
        nm = m.name.decode("utf-8", "replace") if isinstance(m.name, bytes) else m.name
        p = m.filename.decode("utf-8", "replace") if isinstance(m.filename, bytes) else m.filename
        ranges[nm.lower()] = (m.lpBaseOfDll, m.lpBaseOfDll + m.SizeOfImage)
        paths[nm.lower()] = p

    def owner(addr):
        for nm, (lo, hi) in ranges.items():
            if lo <= addr < hi:
                return nm
        return None

    checked = 0
    scanned_modules = []
    hooks = []          # (모듈, 함수, 목적지, 목적지모듈)

    for mod in TARGET_MODULES:
        if mod not in ranges:
            continue
        base, end = ranges[mod]
        try:
            exports = export_table(pm, base)
        except Exception:
            continue
        if not exports:
            continue
        scanned_modules.append(f"{mod}({len(exports)})")

        for name, addr in exports:
            try:
                code = pm.read_bytes(addr, PROLOGUE)
            except Exception:
                continue
            checked += 1
            tgt = resolve_chain(pm, code, addr, owner)
            if tgt is None:
                continue
            # 자기 모듈 안으로 가는 점프는 정상이다 (썽크·ILT 등).
            if base <= tgt < end:
                continue
            hooks.append((mod, name, tgt, owner(tgt)))

    r.meta["target_pid"] = pm.process_id
    r.meta["modules_scanned"] = scanned_modules
    r.meta["exports_checked"] = checked
    r.meta["elapsed_ms"] = int((time.time() - t0) * 1000)

    if checked == 0:
        # 하나도 못 읽었으면 검사한 게 아니다. CLEAN 으로 내보내지 않는다.
        return r.fail("익스포트를 하나도 읽지 못했습니다. "
                      "대상 모듈이 로드되지 않았거나 PE 파싱이 실패했습니다.")

    if not hooks:
        r.detail = (f"모듈 {len(scanned_modules)}개 / 익스포트 {checked:,}개 "
                    f"— 인라인 후킹 없음")
        return r

    # 누가 후킹했는지로 가른다. 스팀 오버레이처럼 서명된 정상 오버레이가
    # Present 를 후킹하는 것은 정상이다. 존재가 아니라 신뢰 근거로 판정한다.
    by_owner = {}
    for mod, name, tgt, own in hooks:
        by_owner.setdefault(own or "알 수 없는 메모리", []).append((mod, name, tgt))

    trusted_note = []
    for own, items in sorted(by_owner.items(), key=lambda kv: -len(kv[1])):
        path = paths.get(own or "", "")
        # verify() 는 "유효" | "서명없음" | "위조" | "신뢰안됨" | "확인불가" 를
        # 돌려준다. **"유효"만 신뢰한다.** "확인불가"를 신뢰 쪽에 넣으면
        # 확인에 실패한 것이 정상으로 뭉개진다.
        sig = verify(path) if path else "확인불가"
        signed = (sig == "유효")

        render = any(m in RENDER_MODULES for m, _, _ in items)
        kind = "렌더링" if render else "일반"
        sample = ", ".join(f"{m}!{n}" for m, n, _ in items[:3])

        if signed:
            trusted_note.append(f"{own} (서명 유효, {kind} {len(items)}건)")
            continue

        r.add("inline_hook_untrusted", 60,
              f"서명 없는 {own} 이 {kind} 함수 {len(items)}개를 인라인 후킹",
              [Evidence("module", own or "?", f"{path} (서명: {sig})"),
               Evidence("value", sample, f"{len(items)}건")])
        if render:
            r.add("overlay_hook", 60,
                  f"렌더링 경로 후킹 — {sample}",
                  [Evidence("module", own or "?", "오버레이/월핵 계열 의심")])

    if trusted_note:
        # 지우지 않고 남긴다. 정상 오버레이가 몇 개 붙어 있는지는
        # 오탐률을 읽을 때 필요한 정보다.
        r.meta["trusted_hookers"] = trusted_note

    if not r.reasons:
        r.detail = (f"인라인 후킹 {len(hooks)}건이 있으나 전부 서명된 모듈 — "
                    + "; ".join(trusted_note))
    return r


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    session = argv[0] if argv else "overlay_001"
    from core.result import to_team_event
    ev = to_team_event(scan(), session)
    print(json.dumps(ev, ensure_ascii=False, indent=2))
    return {"NORMAL": 0, "ERROR": 2, "OFFLINE": 2}.get(ev["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
