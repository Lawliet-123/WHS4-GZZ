"""안티치트 자신이 만든 흔적을 가려내는 곳.

## 왜 필요한가

안티치트가 하는 일과 핵이 하는 일은 **관측 가능한 모양이 같다.**

  - `memory_integrity`·`whistle` 은 pymem 으로 게임 메모리를 읽으려고
    `PROCESS_VM_READ/WRITE` 핸들을 연다 → 외부에서 보면 Cheat Engine 과 같다.
  - 휘파람 관측용 `ac_whistle` DLL 은 게임 안에 들어가 `ExecFunction` 과
    `ProcessInternal` 을 후킹한다 → 우리 자신의 후킹 탐지에 그대로 걸린다.
  - 그 DLL 은 서명이 없고 사용자가 쓸 수 있는 폴더에 있다
    → `모듈 서명 + 경로` 판정에서 `untrusted_module` 이 된다.

2026-09-27 런처로 모듈을 같이 띄운 첫날 실제로 드러났다. 1번 `external_access` 가
우리 `python.exe` 를 `raw_score 8` 로 잡았다. **각자 따로 돌릴 때는 안 보이고
같이 띄워야 드러나는 문제다.**

## 이름으로 빼지 않는다

`ac_whistle*.dll` 이나 `python.exe` 를 이름·해시 allowlist 에 넣는 건 답이 아니다.
그러면 같은 이름을 쓰거나 같은 파이썬으로 짠 핵이 전부 통과한다. 탐지기를 고치려다
구멍을 내는 셈이다.

**"우리 폴더에서 온 것인가"로 본다.** 안티치트 배포본은 `client/` 아래에 있고,
핵은 거기 있을 이유가 없다. `injection.py` 가 이미 쓰는 "서명과 경로로 판정한다"와
같은 기준이다.

## 한계 — 분명히 적어둔다

공격자가 `client/` 안에 파일을 쓸 수 있으면 이 판정을 통과한다. 다만 그 시점이면
안티치트 자체를 고쳐 쓸 수 있으므로 이 검사만의 문제가 아니다. 제대로 막으려면
배포본 서명이나 4번 SelfDefense 의 자체 무결성 검사가 필요하다.

**가려낸 것을 버리지 않는다.** 점수만 빼고 근거에는 남긴다. "우리 후크가 걸려 있다"는
사실 자체는 관측 결과이고, 안 남기면 나중에 "왜 이 세션만 다르지"를 설명할 수 없다.
"""

import os

# 이 파일은 client/LocalGuard/memory_integrity/core/ 에 있다. 세 단계 위가 client/.
SELF_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))


def _norm(p):
    try:
        return os.path.normcase(os.path.abspath(str(p)))
    except Exception:
        return ""


_SELF = _norm(SELF_ROOT)


def is_self_path(path):
    """이 파일이 안티치트 배포본 안에서 온 것인가.

    경로를 모르면(None, 빈 문자열) **우리 것으로 치지 않는다.** 모르는 것을
    자기 것으로 넘기면 조용한 미탐지가 된다.
    """
    if not path:
        return False
    p = _norm(path)
    return bool(p) and (p == _SELF or p.startswith(_SELF + os.sep))
