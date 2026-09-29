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

import hashlib
import json
import os
import sys

# 이 파일을 직접 실행해도 core/ 를 찾게 한다.
# 팀원마다 실행 방식이 달라서 둘 다 되게 해둔다.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from core.result import DetectorResult, Evidence

# ── 런처가 깐 UE4SS 가려내기 ─────────────────────────────────────────────
#
# 우리 팀도 UE4SS 를 쓴다(은지님 DamageLogger, 성민님 GZZPaintObserver).
# 그런데 이 탐지기는 UE4SS 의 존재 자체를 신호로 쓰기 때문에, 런처가 정상
# 설치하면 그 PC 의 정상 세션이 전부 DETECTED(45+50+45 → 100) 로 나온다.
#
# 이름으로 빼지 않는다. dwmapi.dll 이나 UE4SS.dll 을 이름으로 통과시키면 같은
# 이름을 쓰는 핵이 전부 통과한다. UE4SS 는 핵이 제일 많이 쓰는 로더라 더 그렇다.
# **런처가 깔면서 적어 둔 해시와 바이트가 맞는 파일만** 봐준다.
#
# 등록부를 읽는 코드를 여기 따로 둔 이유: 런처 모듈을 import 하면 런처가
# 고장났을 때 탐지기까지 같이 죽는다. 형식은 client/Launcher/ue4ss_manifest.py
# 의 독스트링에 적혀 있다. 읽기만 하므로 표준 라이브러리로 충분하다.
_MANIFEST_ENV = "GZZ_UE4SS_MANIFEST"
_CLIENT_DIR = _os.path.dirname(_os.path.dirname(_os.path.dirname(
    _os.path.dirname(_os.path.abspath(__file__)))))
_DEFAULT_MANIFEST = _os.path.join(_CLIENT_DIR, "Launcher", "logs", "ue4ss_install.json")


def _sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _manifest_path():
    return (_os.environ.get(_MANIFEST_ENV) or "").strip() or _DEFAULT_MANIFEST


def _our_ue4ss(game_dir):
    """(우리 파일 상대경로 집합, 우리 모드 이름 집합, 등록부 상태).

    상태는 "none"(등록부 없음) / "broken"(있는데 못 읽음) / "ok" 다.
    없는 것과 깨진 것을 구분해야 한다 — 깨졌으면 조용히 예전처럼 도는 게
    아니라 그 사실이 근거에 남아야 한다.
    """
    p = _manifest_path()
    if not os.path.isfile(p):
        return set(), set(), "none"
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
        files = m["files"]
        if not isinstance(files, dict):
            raise ValueError("files")
    except (OSError, ValueError, KeyError, TypeError):
        return set(), set(), "broken"
    root = m.get("game_root") or game_dir
    ours = set()
    for r, want in files.items():
        if not isinstance(r, str) or not isinstance(want, str):
            continue
        if _sha256(os.path.join(root, r.replace("/", os.sep))) == want.lower():
            ours.add(r.lower())
    # 모드는 그 모드 폴더 아래 등록된 파일이 **전부** 맞을 때만 우리 것이다.
    mods = set()
    for name in (m.get("mods") or []):
        if not isinstance(name, str):
            continue
        tag = f"/mods/{name.lower()}/"
        listed = [r.lower() for r in files if tag in ("/" + r.lower().replace("\\", "/"))]
        if listed and all(r in ours for r in listed):
            mods.add(name.lower())
    return ours, mods, "ok"

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
    # 런처가 찾아서 알려준 값. 아래 하드코딩 목록보다 먼저 본다 — 런처는
    # 스팀 라이브러리와 떠 있는 프로세스까지 보고 정한다(game_launcher.py).
    env = (os.environ.get("GZZ_GAME_ROOT") or "").strip()
    if env and os.path.isdir(env):
        return env
    for p in _STEAM_HINTS:
        if os.path.isdir(p):
            return p
    # 실행 중이면 프로세스에서 직접 얻는다
    try:
        from core import procopen
        pm = procopen.open_game("PenguinHotel-Win64-Shipping.exe")
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

    # 런처가 깐 UE4SS 는 가려낸다. 해시가 맞는 것만. 자세한 이유는 파일 위쪽 주석.
    ours, our_mods, manifest_state = _our_ue4ss(game_dir)
    r.meta["ue4ss_manifest"] = manifest_state
    exempt = []

    def _rel(ap):
        return os.path.relpath(ap, game_dir).replace("\\", "/").lower()

    # ── 1. 프록시 DLL (UE4SS) ────────────────────────────────────────────
    # `.off` 같은 접미사를 붙여 꺼둔 것도 물증으로 본다. 되돌리는 데 1초다.
    for name in sorted(_PROXY_NAMES):
        for key, orig in lower.items():
            if key == name or key.startswith(name + "."):
                ap = os.path.join(win64, orig)
                if _rel(ap) in ours:
                    exempt.append({"rule": "proxy_dll_in_game_dir", "file": _rel(ap),
                                   "why": "런처 등록부의 해시와 일치"})
                    continue
                disabled = key != name
                r.add("proxy_dll_in_game_dir", 45 if not disabled else 30,
                      f"{orig} 가 게임 Binaries 폴더에 있습니다"
                      + (" (현재는 비활성)" if disabled else ""),
                      [Evidence("file", ap, "시스템 DLL 이름의 사이드로딩 후보")])

    # ── 2. UE4SS 런타임 ──────────────────────────────────────────────────
    ue4ss_dir = os.path.join(win64, "ue4ss")
    if os.path.isdir(ue4ss_dir):
        # 등록부에 이 폴더 파일이 있고, 폴더 안 DLL 이 **하나도 빠짐없이** 우리
        # 것일 때만 봐준다. 우리 UE4SS 옆에 모르는 DLL 을 하나 얹는 수법을
        # 통과시키지 않으려는 것이다.
        pre = _rel(ue4ss_dir) + "/"
        listed = {x for x in ours if x.startswith(pre)}
        try:
            dlls = [f for f in os.listdir(ue4ss_dir)
                    if f.lower().endswith(".dll")
                    and os.path.isfile(os.path.join(ue4ss_dir, f))]
        except OSError:
            dlls = None
        unknown = ([f for f in dlls if _rel(os.path.join(ue4ss_dir, f)) not in ours]
                   if dlls is not None else ["<폴더를 읽지 못함>"])
        if listed and not unknown:
            exempt.append({"rule": "ue4ss_runtime", "file": _rel(ue4ss_dir),
                           "why": f"런처가 설치한 것 (등록 파일 {len(listed)}개 전부 일치)"})
        else:
            ev = [Evidence("file", ue4ss_dir, "UE4SS 런타임 폴더")]
            ini = os.path.join(ue4ss_dir, "UE4SS-settings.ini")
            if os.path.isfile(ini):
                ev.append(Evidence("file", ini, "UE4SS 설정"))
            why = ""
            if listed and unknown:
                why = " (등록부에 없는 DLL: " + ", ".join(sorted(unknown)[:3]) + ")"
                ev.append(Evidence("file", ue4ss_dir, "등록부에 없는 DLL 이 섞여 있습니다"))
            r.add("ue4ss_runtime", 50, "UE4SS 런타임이 설치돼 있습니다" + why, ev)

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
                # 우리 모드는 이름이 아니라 그 폴더 파일 해시가 전부 맞을 때만 뺀다.
                if name.lower() in our_mods:
                    exempt.append({"rule": "third_party_lua_mod", "mod": name,
                                   "why": "런처 등록부의 해시와 일치"})
                    continue
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
        if _rel(os.path.join(win64, e)) in ours:
            exempt.append({"rule": "unexpected_binary_in_game_dir",
                           "file": _rel(os.path.join(win64, e)),
                           "why": "런처 등록부의 해시와 일치"})
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

    # 가려낸 것은 버리지 않는다. 점수만 빼고 근거에는 남긴다 — 안 남기면
    # 나중에 "왜 이 PC 만 점수가 다르지" 를 설명할 수 없다.
    if exempt:
        r.meta["exempt"] = exempt
        r.evidence.append(Evidence(
            "file", _manifest_path(),
            f"런처가 설치한 UE4SS {len(exempt)}건을 점수에서 뺐습니다 (해시 일치)"))
    if manifest_state == "broken":
        # 조용히 예전처럼 돌지 않는다. 못 읽었으면 그 사실이 보여야 한다.
        r.evidence.append(Evidence(
            "file", _manifest_path(),
            "UE4SS 등록부를 읽지 못했습니다 — 우리 UE4SS 도 점수에 들어갑니다"))

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
