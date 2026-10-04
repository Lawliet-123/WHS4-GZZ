"""런처가 설치한 UE4SS 를 **해시로** 알아본다.

## 왜 필요한가

UE4SS 는 Lua 모드를 돌리려고 `UFunction::ExecFunction` 을 후킹한다. 그래서 팀 표준
설치(UE4SS + 팀 모드 4개)가 깔린 PC 는 **정상 세션에서도** `injection` 이
`exec_function_hooked` 70점을 낸다. 10/5 실측(normal_002)에서 3바퀴 전부 그랬고,
대시보드 판정이 SUSPICIOUS(active: injection)로 나왔다.

더 나쁜 건, 휘파람 핵도 ExecFunction 을 후킹한다는 점이다. UE4SS 가 상시 70점을
내면 그 둘을 구분할 수 없다.

## 이름으로 빼지 않는다

`ue4ss.dll` 을 이름으로 통과시키면 같은 이름을 쓰는 핵이 전부 통과한다. UE4SS 는
핵이 제일 많이 쓰는 로더라 더 그렇다. **런처가 깔면서 적어 둔 해시와 바이트가
맞는 파일만** 봐준다. `filesystem.py` 가 쓰는 근거와 같다
(`client/Launcher/logs/ue4ss_install.json`, 형식은 `Launcher/ue4ss_manifest.py`).

## 조용히 빼지 않는다

제외한 것은 호출부가 근거에 남긴다. 상태도 같이 돌려준다 —
"none"(등록부 없음) / "broken"(있는데 못 읽음) / "ok". 등록부가 없거나 깨졌으면
**아무것도 봐주지 않는다.** 그 PC 는 예전처럼 UE4SS 가 그대로 잡힌다.

런처 모듈을 import 하지 않는다. 런처가 고장나도 탐지기는 돌아야 한다.
"""

import hashlib
import json
import os

MANIFEST_ENV = "GZZ_UE4SS_MANIFEST"
_CLIENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_MANIFEST = os.path.join(_CLIENT_DIR, "Launcher", "logs", "ue4ss_install.json")


def manifest_path():
    return (os.environ.get(MANIFEST_ENV) or "").strip() or DEFAULT_MANIFEST


def _sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _load():
    p = manifest_path()
    if not os.path.isfile(p):
        return None, "none"
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
        if not isinstance(m.get("files"), dict):
            raise ValueError("files")
        return m, "ok"
    except (OSError, ValueError, KeyError, TypeError):
        return None, "broken"


def trusted_modules(module_paths, manifest=None):
    """{모듈이름(소문자): 파일경로} -> (신뢰하는 모듈이름 집합, 상태)

    등록부에 적힌 파일이고 **지금 디스크의 해시가 적힌 값과 같을 때만** 신뢰한다.
    경로가 달라도(라이브러리 폴더가 다른 PC) 등록부의 game_root 기준 상대경로로 맞춘다.
    """
    m, state = (manifest, "ok") if manifest else _load()
    if m is None:
        return set(), state

    root = (m.get("game_root") or "").rstrip("\\/")
    want = {}
    for rel, h in m["files"].items():
        if isinstance(rel, str) and isinstance(h, str):
            want[rel.replace("/", os.sep).lower()] = h.lower()

    trusted = set()
    for name, path in module_paths.items():
        if not path:
            continue
        full = os.path.abspath(path)
        rel = None
        if root and full.lower().startswith(root.lower() + os.sep):
            rel = full[len(root) + 1:].lower()
        else:
            # 등록부의 game_root 와 설치 위치가 다를 수 있다. 끝부분으로 맞춘다.
            low = full.lower()
            for r in want:
                if low.endswith(os.sep + r) or low.endswith(r):
                    rel = r
                    break
        if rel is None or rel not in want:
            continue
        if _sha256(full) == want[rel]:
            trusted.add(name.lower())
    return trusted, state


def ranges_of(trusted_names, module_ranges):
    """신뢰 모듈 이름 -> {이름: (시작, 끝)}"""
    return {n: module_ranges[n] for n in trusted_names if n in module_ranges}


def owner_in(addr, ranges):
    """주소가 신뢰 범위 안이면 그 모듈 이름, 아니면 None."""
    for name, (lo, hi) in ranges.items():
        if lo <= addr < hi:
            return name
    return None
