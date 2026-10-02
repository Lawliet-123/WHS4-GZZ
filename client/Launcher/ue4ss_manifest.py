r"""런처가 설치한 UE4SS 가 정확히 무엇인지 남긴다.

## 왜 필요한가

우리 2번 `filesystem` 탐지기는 UE4SS 의 **존재 자체**를 신호로 쓴다. 게임에
`dwmapi.dll` 이 있고 `ue4ss/` 폴더가 있고 외부 Lua 모드가 켜져 있으면
45 + 50 + 45 로 DETECTED(100) 다. 핵이 쓰는 전형적인 구성이라 그렇게 잡는다.

그런데 우리 팀도 UE4SS 를 쓴다. 에임봇(은지님 DamageLogger)·오토페인트(성민님
GZZPaintObserver)·노클립(송희님 NoclipLogger)·갓모드(재민님 GodModeTelemetry)
탐지가 전부 UE4SS 위에서 돈다. 런처가 UE4SS 를
정상 설치하면 **그 PC 의 정상 세션이 전부 DETECTED 로 나온다.** 9/27 에 나온
자기탐지 문제의 세 번째 사례이고, 이번엔 파일 쪽이다.

## 이름으로 빼지 않는다

`dwmapi.dll` 이나 `UE4SS.dll` 을 이름으로 통과시키면, 같은 이름을 쓰는 핵이
전부 통과한다. UE4SS 는 원래 핵이 제일 많이 쓰는 로더라 더더욱 안 된다.
탐지기를 고치려다 구멍을 내는 셈이다.

**"런처가 깐 바로 그 파일인가"** 로 판정한다. 설치할 때 각 파일의 SHA-256 을
여기 적어 두고, 탐지기는 지금 디스크에 있는 파일을 다시 해시해서 대조한다.
바이트가 1개라도 다르면 우리 것이 아니다.

그래서 이 파일이 있다고 봐주는 게 아니다. **이 파일에 적힌 해시와 맞는
파일만** 봐준다. 목록에 없는 프록시 DLL 이 하나라도 있으면 규칙은 그대로 뜬다.

## 한계 (탐지기 쪽 주석과 같은 내용)

공격자가 이 파일을 고쳐 쓸 수 있으면 통과한다. 다만 그 시점이면 안티치트
자체를 고쳐 쓸 수 있으니 이 검사만의 문제는 아니고, 제대로 막으려면 배포본
서명이나 4번 SelfDefense 가 필요하다.

## 파일 모양

`client/Launcher/logs/ue4ss_install.json` (gitignore 대상 — PC 마다 다르다)

    {
      "version": 1,
      "game_root": "C:\\...\\MECCHA CHAMELEON",
      "bundle": {"name": "UE4SS", "version": "3.0.1", "sha256": "..."},
      "files": {
        "Chameleon/Binaries/Win64/dwmapi.dll": "<sha256>",
        "Chameleon/Binaries/Win64/ue4ss/UE4SS.dll": "<sha256>",
        "Chameleon/Binaries/Win64/ue4ss/Mods/DamageLogger/Scripts/main.lua": "<sha256>"
      },
      "mods": ["DamageLogger", "GZZPaintObserver", "NoclipLogger", "GodModeTelemetry"]
    }

경로는 `game_root` 기준 상대경로이고 구분자는 `/` 로 통일한다. 절대경로로
적으면 다른 PC 에서 못 쓰고, 역슬래시는 JSON 에서 두 번 쓰게 되어 실수가 난다.

## 쓰는 쪽 (동효님)

설치가 끝난 직후 한 번 부르면 된다. 설치한 파일 목록만 주면 해시는 여기서 뜬다.

    import ue4ss_manifest
    ue4ss_manifest.record(game_root, installed_paths,
                          bundle={"name": "UE4SS", "version": "...", "sha256": "..."},
                          mods=["DamageLogger", "GZZPaintObserver",
                                "NoclipLogger", "GodModeTelemetry"])

설치한 모드는 전부 넣는다. 빠진 모드는 우리 것이어도 외부 Lua 모드(45점)로 잡힌다.
지우거나 다시 깔면 다시 부르면 된다. 통째로 덮어쓴다.
"""

import hashlib
import json
import os
from typing import Dict, Iterable, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
DEFAULT_PATH = os.path.join(LOG_DIR, "ue4ss_install.json")

# 탐지기는 런처 폴더를 모를 수 있다. 런처가 이 환경변수로 알려준다.
ENV_PATH = "GZZ_UE4SS_MANIFEST"

VERSION = 1


def sha256_of(path: str) -> Optional[str]:
    """파일 해시. 못 읽으면 None — 없는 파일과 못 읽는 파일을 같이 다룬다."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def rel(game_root: str, path: str) -> str:
    """game_root 기준 상대경로를 `/` 구분자로. 밖이면 절대경로 그대로."""
    try:
        r = os.path.relpath(path, game_root)
    except ValueError:                      # 드라이브가 다르면 상대경로가 없다
        return path.replace("\\", "/")
    if r.startswith(".."):
        return path.replace("\\", "/")
    return r.replace("\\", "/")


def path_for(game_root: Optional[str] = None) -> str:
    """등록부 파일 위치. 환경변수가 있으면 그쪽을 쓴다."""
    env = (os.environ.get(ENV_PATH) or "").strip()
    return env or DEFAULT_PATH


def record(game_root: str, paths: Iterable[str], bundle: Optional[dict] = None,
           mods: Optional[List[str]] = None, out_path: Optional[str] = None) -> dict:
    """설치한 파일들을 해시해서 등록부에 남긴다. 통째로 덮어쓴다.

    읽을 수 없는 파일은 조용히 빼지 않고 `unreadable` 에 남긴다. 빠진 파일은
    탐지기가 '우리 것이 아니다' 로 보기 때문에, 왜 빠졌는지 알아야 한다.
    """
    files: Dict[str, str] = {}
    unreadable: List[str] = []
    for p in paths:
        digest = sha256_of(p)
        if digest is None:
            unreadable.append(rel(game_root, p))
        else:
            files[rel(game_root, p)] = digest
    data = {
        "version": VERSION,
        "game_root": game_root,
        "bundle": bundle or {},
        "files": files,
        "mods": list(mods or []),
    }
    if unreadable:
        data["unreadable"] = unreadable
    out = out_path or path_for(game_root)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out)
    return data


def load(path: Optional[str] = None) -> Optional[dict]:
    """등록부를 읽는다. 없거나 깨졌으면 None.

    깨진 등록부를 빈 것으로 다루지 않고 None 을 돌려주는 이유는, 탐지기가
    '등록부가 없다(예전대로 검사한다)' 와 '등록부가 있는데 못 읽었다' 를
    구분할 수 있어야 해서다.
    """
    p = path or path_for()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return None
    return data


def ours(game_root: str, manifest: Optional[dict] = None) -> set:
    """지금 디스크에서 등록부와 **해시까지 맞는** 상대경로들.

    등록부에 적혀 있어도 파일이 바뀌었으면 여기 안 들어간다. 그게 이 구조의
    핵심이다 — 목록에 이름이 있다고 봐주는 게 아니라 바이트가 같아야 봐준다.
    """
    m = manifest if manifest is not None else load()
    if not m:
        return set()
    root = m.get("game_root") or game_root
    out = set()
    for r, want in (m.get("files") or {}).items():
        if not isinstance(want, str):
            continue
        ap = os.path.join(root, r.replace("/", os.sep))
        if sha256_of(ap) == want.lower():
            out.add(r.lower())
    return out
