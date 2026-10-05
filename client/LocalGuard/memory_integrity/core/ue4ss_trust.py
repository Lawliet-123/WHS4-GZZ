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

## 등록부 경로를 환경변수로 받지 않는다

이 등록부는 **무엇을 안 잡을지** 를 정한다. 그래서 경로를 바꿀 수 있는 사람은
탐지기를 끌 수 있는 사람이다. `GZZ_UE4SS_MANIFEST` 로 경로를 받던 때는
자기 `ue4ss.dll` 해시를 적은 등록부를 가리키기만 하면 `filesystem`·`injection`·
`whistle` 셋이 **동시에** 그 파일을 봐줬다. 해시로 가려 놓고 그 해시 목록을
갈아끼울 길을 열어 둔 셈이다.

런처가 자식 프로세스에 환경을 그대로 물려주므로 더 그렇다. #102 의 하네스 PID
환경변수(은지님 지적)와 같은 종류다 — **검증 편의가 회피 경로가 되면 안 된다.**
그래서 경로는 호출부가 **함수 인자로만** 준다. 시험이 가짜 등록부를 쓸 때도
같은 길을 쓴다. 환경변수가 설정돼 있으면 무시하되 `attempted_env_override()` 로
알려서 호출부가 근거에 남긴다.
"""

import hashlib
import json
import os

_CLIENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
MANIFEST_PATH = os.path.join(_CLIENT_DIR, "Launcher", "logs", "ue4ss_install.json")

# 전에 경로를 받던 환경변수. **이제 읽지 않는다.** 이름만 남겨 둔 이유는
# 설정돼 있을 때 그 사실을 근거에 적기 때문이다.
LEGACY_ENV = "GZZ_UE4SS_MANIFEST"


def attempted_env_override():
    """`GZZ_UE4SS_MANIFEST` 가 설정돼 있으면 그 값, 없으면 None.

    판정에는 쓰지 않는다. 호출부가 `meta` 에 적기만 한다 — 등록부를 갈아끼우려는
    시도가 있었는지는 나중에 세션을 다시 읽을 때 필요한 정보다. 점수를 붙이지
    않는 이유는 런처·시험이 과거에 이 변수를 쓴 적이 있어서, 지금 와서 양수로
    만들면 우리 환경이 먼저 걸린다. 사실만 남긴다.
    """
    v = (os.environ.get(LEGACY_ENV) or "").strip()
    return v or None


def _sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def load(path=None):
    """(등록부, 상태). 경로는 **함수 인자로만** 받는다. 환경변수는 안 본다.

    상태는 "none"(없음) / "broken"(있는데 못 읽음) / "ok".
    """
    p = path or MANIFEST_PATH
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


def trusted_modules(module_paths, manifest=None, manifest_path=None):
    """{모듈이름(소문자): 파일경로} -> (신뢰하는 모듈이름 집합, 상태)

    등록부에 적힌 파일이고 **지금 디스크의 해시가 적힌 값과 같을 때만** 신뢰한다.
    경로가 달라도(라이브러리 폴더가 다른 PC) 등록부의 game_root 기준 상대경로로 맞춘다.

    `manifest` 는 이미 읽은 등록부, `manifest_path` 는 읽을 경로다. 둘 다
    **호출부가 명시할 때만** 쓰인다. 기본값은 `MANIFEST_PATH` 하나뿐이다.
    """
    m, state = (manifest, "ok") if manifest else load(manifest_path)
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
