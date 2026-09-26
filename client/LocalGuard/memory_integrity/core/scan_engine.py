"""MECCHA CHAMELEON 4.0.2 외부 안티치트 탐지기 (Ring 3, PoC)

게임 소스가 없어서 안티치트를 본체에 붙일 수 없다. 그래서 별도 프로세스에서
게임을 관찰한다. 우리 팀이 핵을 만들 때 쓴 것과 같은 기법(ReadProcessMemory)을
방어에 그대로 돌려쓰는 구조다.

검사 4종을 동시에 돌린다. 목적은 "다 잡는 것"이 아니라
**어떤 검사가 무엇을 잡고 무엇을 놓치는지 한 화면에서 보이는 것**이다.

  [1] 모듈 이름 화이트리스트 (구방식)  - 오탐 115개. 비교용으로 남겨둔다
  [2] 모듈 서명 + 경로       (WBS 6.3) - 오탐 0개. 실제로 쓰는 판정
  [3] .text 해시             (WBS 6.2) - 코드 섹션 변조를 잡는다
  [4] vtable 무결성          (우리 발견) - ProcessEvent 슬롯 변조를 잡는다
  [5] ExecFunction 무결성    (우리 발견) - 네이티브 UFUNCTION 진입점 교체를 잡는다

실측 결과 (whistle_v14.dll 주입, docs/20 참고):
  [1] 탐지   [2] **미탐지**   [3] 탐지
[2]가 못 잡는 것이 이 프로젝트의 핵심 결과다. vtable 은 .rdata 에 있고
ExecFunction 은 힙에 있다. 둘 다 .text 가 아니라서 코드 해시가 안 변한다.

오프셋은 4.0.2 Dumper-7 덤프 기준이다. 다른 빌드에서는 전부 무효다.

사용법:
    python detector.py                  # 기준선 없이 1회 검사
    python detector.py <text_sha256>    # .text 해시를 기준선과 비교
"""

import hashlib
import os
import struct
import sys

import pymem

from core import selfid, signature

GAME_EXE = "PenguinHotel-Win64-Shipping.exe"

# ── 4.0.2 전용 오프셋 ────────────────────────────────────────────────────
GOBJECTS_RVA = 0x095DC5A0   # FUObjectArray (모듈 베이스로부터의 RVA)
PROCESS_EVENT_IDX = 0x4C    # UObject vtable 에서 ProcessEvent 의 슬롯 번호
CLASS_OFFSET = 0x10         # UObject::Class
EXEC_FUNCTION_OFF = 0xD8    # UFunction::ExecFunction - 네이티브 진입점
OBJ_HEADER_SPAN = 0xE0      # vtable(0x00) + Class(0x10) + ExecFunction(0xD8) 를 한 번에

# FUObjectArray 내부 레이아웃 (godmode 모듈에서 검증된 값)
GOBJ_CHUNKS = 0x00          # void*** - 청크 배열
GOBJ_NUM = 0x14             # int32   - 총 오브젝트 수
CHUNK_SIZE = 65536          # 청크당 오브젝트 수
ITEM_STRIDE = 0x18          # FUObjectItem 크기

# 정상 세션에서 관측된 모듈. 여기 없으면 의심한다.
# NOTE: 이 목록은 임시다. 1차 실측에서 81개가 오탐으로 잡혔다(Intel/NVIDIA 드라이버,
#       Steam, DirectML 등). 정상 세션을 반복 수집해 기준선을 다시 만들어야 한다.
#       PC 마다 하드웨어·오버레이 구성이 달라 목록이 달라지는 것이 이 방식의 구조적 한계다.
KNOWN_MODULES = {
    "penguinhotel-win64-shipping.exe", "tbb12.dll", "tbbmalloc.dll",
    "ntdll.dll", "kernel32.dll", "kernelbase.dll", "user32.dll", "gdi32.dll",
    "gdi32full.dll", "msvcp_win.dll", "ucrtbase.dll", "advapi32.dll",
    "msvcrt.dll", "sechost.dll", "rpcrt4.dll", "shell32.dll", "ole32.dll",
    "combase.dll", "oleaut32.dll", "shlwapi.dll", "ws2_32.dll", "winmm.dll",
    "imm32.dll", "setupapi.dll", "cfgmgr32.dll", "bcrypt.dll", "bcryptprimitives.dll",
    "crypt32.dll", "wintrust.dll", "version.dll", "psapi.dll", "dbghelp.dll",
    "d3d11.dll", "d3d12.dll", "dxgi.dll", "d3d12core.dll", "dxcore.dll",
    "xinput1_3.dll", "xinput1_4.dll", "dinput8.dll", "hid.dll",
    "opengl32.dll", "glu32.dll", "dwmapi.dll",
    "steam_api64.dll", "gameoverlayrenderer64.dll", "steamclient64.dll",
}


class Detection:
    """검사 하나의 결과. 탐지 여부와 근거를 같이 들고 다닌다."""

    def __init__(self, check, caught, detail):
        self.check = check
        self.caught = caught
        self.detail = detail


def owner_of(addr, module_ranges):
    """주소가 어느 모듈에 속하는지 역추적한다. 책임 DLL 이름이 그대로 나온다."""
    for name, (lo, hi) in module_ranges.items():
        if lo <= addr < hi:
            return name
    return "알 수 없는 메모리"


# ── PE 헤더 파싱 ─────────────────────────────────────────────────────────
def read_sections(pm, base):
    """메모리에 매핑된 PE 이미지에서 섹션 테이블을 읽는다.

    디스크 파일이 아니라 메모리를 읽는 이유: 검사 대상은 '지금 실행 중인 코드'다.
    """
    e_lfanew = struct.unpack("<I", pm.read_bytes(base + 0x3C, 4))[0]
    nt = base + e_lfanew
    num_sections = struct.unpack("<H", pm.read_bytes(nt + 0x06, 2))[0]
    opt_size = struct.unpack("<H", pm.read_bytes(nt + 0x14, 2))[0]
    sect_table = nt + 0x18 + opt_size

    sections = {}
    for i in range(num_sections):
        raw = pm.read_bytes(sect_table + i * 40, 40)
        name = raw[:8].rstrip(b"\x00").decode("ascii", "replace")
        virtual_size, virtual_addr = struct.unpack("<II", raw[8:16])
        sections[name] = (base + virtual_addr, virtual_size)
    return sections


# ── GObjects 단일 순회 ───────────────────────────────────────────────────
def scan_objects(pm, base):
    """살아있는 UObject 를 전부 훑어 헤더를 모은다.

    오브젝트당 RPM 을 3번 하면 6만 개에 18만 번이다. 헤더가 연속이라
    0xE0 바이트를 한 번에 읽어 vtable/Class/ExecFunction 을 동시에 뽑는다.
    UObject 는 0xE0 보다 작을 수 있으므로 실패하면 짧게 다시 읽는다.
    """
    gobjects = base + GOBJECTS_RVA
    chunks_ptr = struct.unpack("<Q", pm.read_bytes(gobjects + GOBJ_CHUNKS, 8))[0]
    num = struct.unpack("<i", pm.read_bytes(gobjects + GOBJ_NUM, 4))[0]
    if not chunks_ptr or num <= 0 or num > 4_000_000:
        return None, 0

    rows = []          # (vtable, class_ptr, exec_fn 또는 None)
    chunk_cache = {}
    for i in range(num):
        ci = i // CHUNK_SIZE
        if ci not in chunk_cache:
            try:
                chunk_cache[ci] = struct.unpack("<Q", pm.read_bytes(chunks_ptr + ci * 8, 8))[0]
            except Exception:
                break
        chunk = chunk_cache[ci]
        if not chunk:
            continue
        try:
            obj = struct.unpack("<Q", pm.read_bytes(chunk + (i % CHUNK_SIZE) * ITEM_STRIDE, 8))[0]
        except Exception:
            continue
        if not obj:
            continue
        try:
            hdr = pm.read_bytes(obj, OBJ_HEADER_SPAN)
            exec_fn = struct.unpack("<Q", hdr[EXEC_FUNCTION_OFF:EXEC_FUNCTION_OFF + 8])[0]
        except Exception:
            try:
                hdr = pm.read_bytes(obj, 0x18)
                exec_fn = None
            except Exception:
                continue
        vtable = struct.unpack("<Q", hdr[0:8])[0]
        cls = struct.unpack("<Q", hdr[CLASS_OFFSET:CLASS_OFFSET + 8])[0]
        if vtable:
            rows.append((vtable, cls, exec_fn))
    return rows, num


# ── [1] 모듈 이름 화이트리스트 (구방식, 비교용) ──────────────────────────
def check_module_names(pm):
    """이름 목록과 대조한다.

    1차 실측에서 오탐 115개를 냈다. PC 마다 드라이버·오버레이 구성이 달라서
    목록을 손으로 관리하는 한 이 문제는 안 없어진다. 실제 판정에는 안 쓰고
    **서명 방식과 비교해 보여주려고** 남겨둔다.
    """
    unknown = [m for m in _module_rows(pm) if m[0].lower() not in KNOWN_MODULES]
    if not unknown:
        return Detection("모듈 이름 화이트리스트", False, "알 수 없는 모듈 없음")
    return Detection("모듈 이름 화이트리스트", True,
                     f"미확인 {len(unknown)}건 — 이 중 대부분이 정상 모듈이다 (오탐)")


# ── [2] 모듈 서명 + 경로 ─────────────────────────────────────────────────
def _self_bases(pm):
    """안티치트 자신의 모듈이 올라간 주소 범위. 자기 후크를 핵으로 세지 않으려고."""
    out = []
    for mod in pm.list_modules():
        f = getattr(mod, "filename", None)
        if isinstance(f, bytes):
            f = f.decode("utf-8", "replace")
        if selfid.is_self_path(f):
            out.append((mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage))
    return out


def _is_self_addr(addr, self_ranges):
    return any(lo <= addr < hi for lo, hi in self_ranges)


def check_module_trust(pm, game_dir):
    """서명과 경로로 판정한다.

    이름이 아니라 "누가 서명했고 어디서 왔는가"를 본다. 환경이 달라져도
    이 기준은 안 흔들리기 때문에 오탐이 0 이다.
    """
    rows = _module_rows(pm)
    signature.prewarm([p for _n, p in rows])

    suspects, cautions, mine = [], 0, 0
    for name, path in rows:
        if selfid.is_self_path(path):
            # 우리 관측용 DLL 은 서명이 없고 사용자 폴더에 있어 반드시 "의심"이 된다.
            # 이름으로 빼면 같은 이름을 쓴 핵이 통과하므로 **출처**로 가른다.
            mine += 1
            continue
        grade, why = signature.verdict(path, game_dir)
        if grade == "의심":
            suspects.append((name, why, path))
        elif grade == "주의":
            cautions += 1

    tail = f" (주의 {cautions}건" + (f", 안티치트 자체 {mine}건" if mine else "") + ")"
    if not suspects:
        return Detection("모듈 서명 + 경로", False,
                         f"모듈 {len(rows)}개 - 의심 0건" + tail)

    lines = []
    for name, why, path in suspects:
        lines.append(f"{name}  —  {why}")
        lines.append(f"    {path}")
    return Detection("모듈 서명 + 경로", True,
                     f"모듈 {len(rows)}개 중 의심 {len(suspects)}건\n      "
                     + "\n      ".join(lines))


def _module_rows(pm):
    out = []
    for mod in pm.list_modules():
        name = mod.name if isinstance(mod.name, str) else mod.name.decode("utf-8", "replace")
        path = mod.filename if isinstance(mod.filename, str) else (mod.filename or b"").decode("utf-8", "replace")
        out.append((name, path))
    return out


# ── [2] .text 해시 ───────────────────────────────────────────────────────
def check_text_hash(pm, base, baseline=None):
    """코드 섹션을 해시한다.

    한계가 분명하다. vtable 은 .rdata 에 있고 UFunction::ExecFunction 은
    힙에 있다. 둘 다 .text 가 아니다. 즉 이 검사는 inline 후킹만 잡는다.
    """
    sections = read_sections(pm, base)
    if ".text" not in sections:
        return Detection(".text 해시", False, ".text 섹션을 못 찾음")

    addr, size = sections[".text"]
    digest = hashlib.sha256()
    remaining, cur = size, addr
    while remaining > 0:
        n = min(0x100000, remaining)
        digest.update(pm.read_bytes(cur, n))
        cur += n
        remaining -= n
    h = digest.hexdigest()

    if baseline is None:
        return Detection(".text 해시", False,
                         f"기준값 없음 - 이번 값을 기준선으로 기록\n      sha256={h}")
    if h != baseline:
        return Detection(".text 해시", True,
                         f"코드 섹션 변조\n      기준={baseline}\n      현재={h}")
    return Detection(".text 해시", False, f"일치 sha256={h[:32]}...")


# ── [3] vtable 무결성 ────────────────────────────────────────────────────
def check_vtable(pm, rows, module_ranges):
    """UObject vtable 의 ProcessEvent 슬롯을 검사한다.

    원리: 정상이라면 이 포인터는 게임 실행파일 안을 가리킨다. 후킹되면
    주입된 DLL 안을 가리키므로 게임 모듈 범위를 벗어난다. 범위로 보기
    때문에 기준선 스냅샷이 필요 없다.

    주의: AActor 가 UObject::ProcessEvent 를 오버라이드한다. 정상 상태에서도
    같은 슬롯에 구현이 여러 개다. 값이 하나라고 가정하면 오탐이 난다.
    """
    game_lo, game_hi = module_ranges[GAME_EXE.lower()]
    self_ranges = _self_bases(pm)

    # vtable 은 클래스당 하나라 오브젝트 수보다 훨씬 적다. 중복 읽기를 없앤다.
    seen = {}
    for vtable, _cls, _e in rows:
        seen[vtable] = seen.get(vtable, 0) + 1

    outside, checked = {}, 0
    for vtable, cnt in seen.items():
        try:
            fn = struct.unpack("<Q", pm.read_bytes(vtable + PROCESS_EVENT_IDX * 8, 8))[0]
        except Exception:
            continue
        checked += cnt
        if not (game_lo <= fn < game_hi) and not _is_self_addr(fn, self_ranges):
            outside[fn] = outside.get(fn, 0) + cnt

    if not outside:
        return Detection("vtable 무결성", False,
                         f"오브젝트 {checked:,}개 / vtable {len(seen)}종 - ProcessEvent 전부 게임 모듈 내부")

    lines = [f"0x{fn:016X}  ({owner_of(fn, module_ranges)})  오브젝트 {cnt:,}개"
             for fn, cnt in sorted(outside.items(), key=lambda kv: -kv[1])]
    return Detection("vtable 무결성", True,
                     f"vtable {len(seen)}종 중 게임 모듈 밖을 가리키는 ProcessEvent 발견\n      "
                     + "\n      ".join(lines))


# ── [4] ExecFunction 무결성 ──────────────────────────────────────────────
def check_exec_function(pm, rows, module_ranges):
    """UFunction::ExecFunction(+0xD8) 포인터 교체를 검사한다.

    이건 vtable 검사로도 안 잡힌다. 네이티브 UFUNCTION 은 ProcessEvent 를
    거치지 않고 이 포인터로 바로 들어가기 때문이다. 휘파람 핵이 소리를
    바꾸는 데 쓴 세 번째 기법이 이것이다.

    어려운 점: 외부에서 "이 오브젝트가 UFunction 인가"를 어떻게 아는가.
    이름 해석(GNames 순회)은 비싸다. 대신 클래스 포인터로 묶어서 자가 보정한다.
    같은 클래스의 멤버 대다수가 +0xD8 에 게임 코드 주소를 갖고 있으면 그
    클래스는 UFunction 계열이고, 그 안에서 혼자 바깥을 가리키는 놈이 후킹된 것이다.

    한계: 한 클래스의 멤버가 전부 후킹되면 다수결이 뒤집혀 못 잡는다.
    """
    game_lo, game_hi = module_ranges[GAME_EXE.lower()]

    groups = {}   # class_ptr -> [내부 개수, [바깥 주소들]]
    for _vt, cls, exec_fn in rows:
        if exec_fn is None or not cls or exec_fn < 0x10000:
            continue
        g = groups.setdefault(cls, [0, []])
        if game_lo <= exec_fn < game_hi:
            g[0] += 1
        else:
            g[1].append(exec_fn)

    suspects, fn_like = {}, 0
    for _cls, (inside, outs) in groups.items():
        total = inside + len(outs)
        if total < 20 or inside / total < 0.9:
            continue                      # UFunction 계열로 보기엔 근거가 약하다
        fn_like += total
        for a in outs:
            suspects[a] = suspects.get(a, 0) + 1

    if not fn_like:
        return Detection("ExecFunction 무결성", False,
                         "UFunction 계열 클래스를 식별하지 못했습니다 - 오프셋 확인 필요")
    if not suspects:
        return Detection("ExecFunction 무결성", False,
                         f"UFunction 계열 {fn_like:,}개 - ExecFunction 전부 게임 모듈 내부")

    lines = [f"0x{a:016X}  ({owner_of(a, module_ranges)})  {c}개"
             for a, c in sorted(suspects.items(), key=lambda kv: -kv[1])]
    return Detection("ExecFunction 무결성", True,
                     f"UFunction 계열 {fn_like:,}개 중 게임 모듈 밖을 가리키는 ExecFunction 발견\n      "
                     + "\n      ".join(lines))


def main():
    baseline = sys.argv[1] if len(sys.argv) > 1 else None

    try:
        pm = pymem.Pymem(GAME_EXE)
    except Exception as e:
        print(f"[!] 게임에 붙지 못했습니다: {e}")
        print(f"    {GAME_EXE} 가 실행 중인지 확인하세요.")
        return 1

    base = pm.process_base.lpBaseOfDll

    module_ranges = {}
    for mod in pm.list_modules():
        name = mod.name.decode("utf-8", "replace") if isinstance(mod.name, bytes) else mod.name
        module_ranges[name.lower()] = (mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage)

    print("=" * 74)
    print(f"  MECCHA CHAMELEON 외부 탐지기 (Ring 3)   pid={pm.process_id}  base=0x{base:X}")
    print("=" * 74)

    rows, total = scan_objects(pm, base)
    if rows is None:
        print("\n  [!] GObjects 를 읽지 못했습니다. 오프셋이 이 빌드와 안 맞습니다.")
        return 1
    print(f"  GObjects {total:,}개 중 {len(rows):,}개 읽음")

    game_dir = None
    for name, path in _module_rows(pm):
        if name.lower() == GAME_EXE.lower():
            game_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(path))))
            break

    results = [
        check_module_names(pm),
        check_module_trust(pm, game_dir),
        check_text_hash(pm, base, baseline),
        check_vtable(pm, rows, module_ranges),
        check_exec_function(pm, rows, module_ranges),
    ]

    for r in results:
        mark = "탐지" if r.caught else "  - "
        print(f"\n  [{mark}] {r.check}")
        print(f"      {r.detail}")

    caught = [r.check for r in results if r.caught]
    print("\n" + "-" * 74)
    if caught:
        print(f"  결과: {len(caught)}/5 탐지  ->  " + ", ".join(caught))
    else:
        print("  결과: 0/5 탐지 - 깨끗하거나, 탐지 못하는 기법이 쓰였습니다")
    print("-" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
