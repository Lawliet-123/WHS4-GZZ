"""A-1 파일시스템 아티팩트 스캔 (WBS 6.3 / 설계서 P0 1순위)

게임 프로세스가 **뜨기도 전에** 판정이 끝난다. 메모리 검사와 달리
오프셋에 의존하지 않으므로 게임이 업데이트돼도 안 깨진다.

설계서(docs/21 §6.2)가 이걸 P0 1순위로 꼽은 이유:
  - 공수 0.5일, 오탐 거의 0
  - 8종 중 4종을 잡는다 (UE4SS, godmode, noclip, whistle)
  - 데모가 없어도 멧챠 본체에 바로 돌아간다

근거가 되는 실측: 우리 설치본의 `Chameleon\\Binaries\\Win64\\` 에
`dwmapi.dll.off` 가 실제로 있다. UE4SS 를 껐다 켰다 하려고 이름만
바꿔둔 것인데, 스캐너 입장에서는 그 자체가 물증이다.

## 한계 (먼저 적는다)

  - 실행 직전에 복사하고 실행 후 지우면 스캔 시점과 레이스가 난다
  - 파일명을 바꾸면 이름 기반 규칙은 빠져나간다
  - auto-paint ver2 는 `%LOCALAPPDATA%` 하위 무작위 경로를 쓴다
  - aimbot / esp 는 게임 폴더에 아무것도 두지 않는다 → **원리적으로 못 잡는다**

그래서 이건 단독 방어가 아니라 **가장 싸게 4종을 걷어내는 1차 필터**다.
"""

import os
import sys

# 이 파일을 직접 실행해도 core/ 를 찾게 한다.
# 팀원마다 실행 방식이 달라서 둘 다 되게 해둔다.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from core.result import DetectorResult, Evidence

# 게임 설치 폴더 자동 탐지에 쓸 후보 (Steam 기본 + 흔한 라이브러리 위치)
_STEAM_HINTS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\MECCHA CHAMELEON",
    r"D:\SteamLibrary\steamapps\common\MECCHA CHAMELEON",
    r"E:\SteamLibrary\steamapps\common\MECCHA CHAMELEON",
]

WIN64 = os.path.join("Chameleon", "Binaries", "Win64")

# 게임이 정품 상태에서 Win64 폴더에 두는 파일. 이 밖의 DLL 은 설명이 필요하다.
_STOCK_WIN64 = {
    "penguinhotel-win64-shipping.exe", "tbb12.dll", "tbbmalloc.dll",
    "steam_appid.txt",
}

# UE4SS 가 사이드로딩에 쓰는 이름들. 게임이 동봉하지 않는 시스템 DLL 이름이라
# Win64 폴더에 있으면 그 자체로 비정상이다.
_PROXY_NAMES = {
    "dwmapi.dll", "xinput1_3.dll", "d3d11.dll", "dinput8.dll",
    "version.dll", "winmm.dll", "dsound.dll",
}


def find_game_dir(explicit=None):
    if explicit and os.path.isdir(explicit):
        return explicit
    for p in _STEAM_HINTS:
        if os.path.isdir(p):
            return p
    # 실행 중이면 프로세스에서 직접 얻는다
    try:
        import pymem
        pm = pymem.Pymem("PenguinHotel-Win64-Shipping.exe")
        exe = pm.process_base.filename
        exe = exe if isinstance(exe, str) else exe.decode("utf-8", "replace")
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(exe))))
    except Exception:
        return None


def _walk_names(root, limit=4000):
    """폴더를 훑어 (상대경로, 절대경로) 를 낸다. 무한 순회를 막으려 상한을 둔다."""
    out, n = [], 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            ap = os.path.join(dirpath, f)
            out.append((os.path.relpath(ap, root), ap))
            n += 1
            if n >= limit:
                return out
    return out


def scan(game_dir=None):
    r = DetectorResult("filesystem")

    game_dir = find_game_dir(game_dir)
    if not game_dir:
        return r.fail("게임 설치 폴더를 찾지 못했습니다. 경로를 인자로 넘기세요.")
    r.meta["game_dir"] = game_dir

    win64 = os.path.join(game_dir, WIN64)
    if not os.path.isdir(win64):
        return r.fail(f"Win64 폴더가 없습니다: {win64}")

    entries = os.listdir(win64)
    lower = {e.lower(): e for e in entries}

    # ── 1. 프록시 DLL (UE4SS) ────────────────────────────────────────────
    # `.off` 같은 접미사를 붙여 꺼둔 것도 물증으로 본다. 되돌리는 데 1초다.
    for name in sorted(_PROXY_NAMES):
        for key, orig in lower.items():
            if key == name or key.startswith(name + "."):
                ap = os.path.join(win64, orig)
                disabled = key != name
                r.add("proxy_dll_in_game_dir", 45 if not disabled else 30,
                      f"{orig} 가 게임 Binaries 폴더에 있습니다"
                      + (" (현재는 비활성)" if disabled else ""),
                      [Evidence("file", ap, "시스템 DLL 이름의 사이드로딩 후보")])

    # ── 2. UE4SS 런타임 ──────────────────────────────────────────────────
    ue4ss_dir = os.path.join(win64, "ue4ss")
    if os.path.isdir(ue4ss_dir):
        ev = [Evidence("file", ue4ss_dir, "UE4SS 런타임 폴더")]
        ini = os.path.join(ue4ss_dir, "UE4SS-settings.ini")
        if os.path.isfile(ini):
            ev.append(Evidence("file", ini, "UE4SS 설정"))
        r.add("ue4ss_runtime", 50, "UE4SS 런타임이 설치돼 있습니다", ev)

    # ── 3. 활성화된 Lua 모드 ─────────────────────────────────────────────
    for mods_txt in (os.path.join(ue4ss_dir, "Mods", "mods.txt"),
                     os.path.join(win64, "Mods", "mods.txt")):
        if not os.path.isfile(mods_txt):
            continue
        try:
            lines = open(mods_txt, encoding="utf-8", errors="replace").read().splitlines()
        except Exception:
            continue
        # UE4SS 기본 동봉 모드는 정상이다. 그 밖의 활성 모드만 신호로 본다.
        stock = {"checkmanagerenablermod", "cheatmanagerenablermod", "consolecommandsmod",
                 "consoleenablermod", "splitscreenmod", "linetracemod",
                 "bpml_genericfunctions", "bpmodloadermod", "keybinds"}
        extra = []
        for ln in lines:
            ln = ln.strip()
            if not ln or ln.startswith(";") or ":" not in ln:
                continue
            name, _, state = ln.partition(":")
            name, state = name.strip(), state.strip()
            if state == "1" and name.lower() not in stock:
                extra.append(name)
        if extra:
            r.add("third_party_lua_mod", 45,
                  "UE4SS 에 외부 Lua 모드가 활성화돼 있습니다: " + ", ".join(extra),
                  [Evidence("file", mods_txt, f"활성 모드 {len(extra)}개")])

    # ── 4. 알려진 치트 아티팩트 (이름 고정) ──────────────────────────────
    KNOWN = [
        ("GodModeHost402.on", "godmode_artifact", 50, "godmode 활성화 플래그 파일"),
        ("GodModeHost402.log", "godmode_artifact", 40, "godmode 실행 로그"),
        ("Dumper-7.ini", "dumper_artifact", 35, "Dumper-7 SDK 덤퍼 설정"),
    ]
    for rel, ap in _walk_names(game_dir):
        base = os.path.basename(ap).lower()
        for fname, code, pts, note in KNOWN:
            if base == fname.lower():
                r.add(code, pts, f"{fname} 발견", [Evidence("file", ap, note)])

    # ── 5. Win64 폴더의 설명되지 않는 DLL ────────────────────────────────
    # 프록시 이름이 아니어도, 정품에 없던 DLL 이 실행파일 옆에 있으면 신호다.
    extra_dll = []
    for e in entries:
        low = e.lower()
        if not low.endswith((".dll", ".exe")):
            continue
        if low in _STOCK_WIN64 or low in _PROXY_NAMES:
            continue
        if low.split(".")[0] + ".dll" in _PROXY_NAMES:
            continue
        extra_dll.append(e)
    if extra_dll:
        r.add("unexpected_binary_in_game_dir", 25,
              "정품에 없던 실행 파일이 게임 폴더에 있습니다: " + ", ".join(extra_dll[:5]),
              [Evidence("file", os.path.join(win64, e)) for e in extra_dll[:5]])

    # ── 6. 게임 폴더 밖의 고정 흔적 ──────────────────────────────────────
    for p, code, pts, note in [
        (r"C:\Dumper-7", "dumper_output_dir", 30, "Dumper-7 덤프 산출물 폴더"),
        (r"C:\Dumper-7\whistle-pe-log.txt", "whistle_log", 45, "휘파람 핵 ProcessEvent 로그"),
    ]:
        if os.path.exists(p):
            r.add(code, pts, f"{os.path.basename(p)} 발견", [Evidence("file", p, note)])

    if not r.reasons:
        r.detail = f"게임 폴더에서 알려진 치트 흔적을 찾지 못했습니다 ({game_dir})"
    return r


def main():
    import json
    res = scan(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    return 0 if res.result in ("CLEAN",) else 1


if __name__ == "__main__":
    sys.exit(main())
