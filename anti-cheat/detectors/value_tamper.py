"""값 변조 탐지 — 역할표 2번의 B축

2번은 두 축이다. A축(코드 변조·후킹)은 `whistle_detector.py` 와 `detector.py` 가
맡는다. 이 파일이 나머지 절반, **"기존 치트의 대상 값 감시"** 다.

A축은 후킹 여부만 보므로 핵 종류를 가리지 않았다. B축은 반대다.
핵마다 건드리는 필드가 다르므로 **대상을 하나씩 알아야 한다.**
대상 목록은 심재민이 2026-09-15 정리한 "ZIP 실제 코드 기준" 표에서 가져왔고,
오프셋은 Dumper-7 CppSDK 에서 직접 확인했다.

## 담당 범위 (2026-09-15 심재민과 분담)

이 파일은 **내 담당 4종**만 다룬다.

    내 담당    휘파람 조작 · Hide Anywhere · Auto Paint v1 · ESP
    심재민     GodMode · Noclip · Auto Paint v2 · AIMBOT

휘파람은 `whistle_detector.py` 가 따로 맡는다(A축). 여기 남는 것은
Hide Anywhere 와 Auto Paint v1 이다. ESP 는 아래에 적은 이유로 검사할 것이 없다.

GodMode 상태 검사(`Dead` `Invincible` `Health`)는 처음에 여기 있었으나
분담 후 심재민 쪽으로 넘겼다. 찾아둔 오프셋은 아래 주석에 남겨둔다.

## 기준값을 저장하지 않는다

값 변조를 잡으려면 "원래 얼마였나"를 알아야 한다. 상수로 박아두면 게임이
패치될 때마다 틀리고, 틀린 줄도 모른 채 통과한다 — 조용한 미탐지다.

그래서 **기준을 게임 메모리에서 읽는다.** 언리얼은 기본값 인스턴스를
들고 있으니 살아있는 인스턴스와 그것을 비교한다. A축이 "게임 모듈 안이냐
밖이냐"만 보고 정상 값을 저장하지 않았던 것과 같다.

### 기준은 CDO 가 아니라 아키타입이다

처음엔 `UClass::ClassDefaultObject`(CDO)만 기준으로 썼다. **깨끗한 세션에서
오탐이 났다.**

    bAutoFlushStrokes   실제=False   CDO=True    RuntimePaintable
    bAutoFlushStrokes   실제=False   CDO=True    RuntimePaintable_GEN_VARIABLE

`_GEN_VARIABLE` 은 블루프린트가 들고 있는 **컴포넌트 템플릿(아키타입)** 이다.
C++ 클래스의 CDO 가 True 라도 블루프린트가 False 로 덮어쓸 수 있고,
살아있는 인스턴스는 CDO 가 아니라 **그 아키타입에서 복사되어 생성된다.**
즉 CDO 와 다른 것이 정상이었다.

그래서 같은 클래스의 아키타입이 있으면 그것을 기준으로 쓰고, 없을 때만
CDO 로 물러난다. 기준을 메모리에서 읽는다는 원칙은 그대로다.

## 다만 CDO 비교가 성립하는 필드만 넣는다

체력처럼 게임 플레이로 변하는 값은 CDO 와 다른 것이 정상이다. 넣으면 전원이
걸린다. 복제되는 값(`Net` `RepNotify`)도 서버가 런타임에 바꾸므로 마찬가지다.

    넣는다     런타임에 바뀌면 안 되는 설정값   → CDO 와 다르면 변조
    안 넣는다  게임 플레이·복제로 바뀌는 값      → 불변식으로 봐야 한다

무엇을 어느 쪽으로 뒀는지는 `CONFIG_FIELDS` 아래 주석에 적어뒀다.

## 못 잡는 것 (내 담당 중)

  - **ESP** 는 외부에서 읽기만 한다. **바꾸는 값이 없어 B축에 걸릴 것이 없고,
    코드도 안 건드리므로 A축에도 안 걸린다.** 2번 레이어에서는 원리적으로
    탐지 불가다. 게임 프로세스를 여는 핸들을 감시하는 1번이라야 한다.
  - **Auto Paint v1 의 호출 자체** — 제한값을 안 건드리고 정상 속도로
    자동 클릭만 하면 여기 걸리지 않는다. 그때는 RPC 호출 패턴을 봐야 하고
    그건 `rpc_report.py` + 인프로세스 후크 쪽이다.

## 남의 담당 (참고)

  - **에임봇** `AController::ControlRotation` 0x0320 은 매 프레임 정상적으로
    바뀐다. 스냅샷 한 장으로는 구분 불가고 Raw Input 비교가 필요하다.
"""

import json
import struct
import sys
import time

import pymem.exception

# 이 파일을 직접 실행해도 core/ 를 찾게 한다.
# 팀원마다 실행 방식이 달라서 둘 다 되게 해둔다.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from core.result import DetectorResult, Evidence
from core.unreal import Runtime

# ── 대상 필드 (CppSDK 4.0.2 에서 확인) ──────────────────────────────────
#
# (클래스, 필드명, 오프셋, 타입, 어느 핵이 건드리나)
#
# 타입 b=bool i=int32 d=double
# 클래스는 상속 사슬로 매칭한다. 블루프린트가
# `..._Survivor_Default_Fukuyoka_1point4_C` 처럼 파생돼 있어서
# 이름을 정확히 맞추려 하면 자식 클래스를 전부 놓친다.

# **SDK 헤더 이름과 런타임 이름이 다르다.**
# Dumper-7 은 헤더에 언리얼 타입 접두사를 붙이지만(Actor 는 A, UObject 는 U)
# 런타임 FName 에는 그 글자가 없다.
#
#   헤더  ABP_FirstPersonCharacter_Main_C   UBPC_NearInteract_C
#   런타임 BP_FirstPersonCharacter_Main_C    BPC_NearInteract_C
#
# 여기에는 **런타임 이름**을 적는다. 다만 팀원이 헤더에서 그대로 복사해
# 넣어도 조용히 안 걸리는 일이 없도록 `_in_chain()` 이 접두사를 무시한다.
CHARACTER = "BP_FirstPersonCharacter_Main_C"
SURVIVOR = "BP_FirstPersonCharacter_cLeon_Character_Survivor_C"
NEAR_INTERACT = "BPC_NearInteract_C"
PAINTABLE = "RuntimePaintableComponent"

CONFIG_FIELDS = [
    # ── Hide Anywhere ────────────────────────────────────────────────
    # 상호작용 거리·탐색 반경·각도를 키워서 멀리서, 아무 데나 숨는다.
    (CHARACTER,     "InteractLength", 0x0510, "d", "Hide Anywhere"),
    (CHARACTER,     "EnableInteract", 0x0676, "b", "Hide Anywhere"),
    (SURVIVOR,      "PreStencil",     0x0D20, "i", "Hide Anywhere"),
    (NEAR_INTERACT, "SearchRadius",   0x00C0, "d", "Hide Anywhere"),
    (NEAR_INTERACT, "Angle",          0x00C8, "d", "Hide Anywhere"),

    # ── Auto Paint v1 ────────────────────────────────────────────────
    # 칠하기에는 **속도 제한이 전부 설정값으로 노출돼 있다.** 봇이 빨리
    # 칠하려면 이걸 풀어야 하고, 전부 CDO 대조가 그대로 성립한다.
    # MinScreenPaintDistance 를 0 으로 만들면 한 점에서 무한히 칠할 수 있다.
    (PAINTABLE, "MinScreenPaintDistance",           0x0138, "f", "Auto Paint v1"),
    (PAINTABLE, "MaxBatchSize",                     0x01EC, "i", "Auto Paint v1"),
    (PAINTABLE, "MaxNetworkBatchesPerTick",         0x01F0, "i", "Auto Paint v1"),
    (PAINTABLE, "MaxReplicatedPaintStrokesPerTick", 0x01F4, "i", "Auto Paint v1"),
    (PAINTABLE, "AutoFlushThreshold",               0x01E8, "i", "Auto Paint v1"),
    (PAINTABLE, "bAutoFlushStrokes",                0x01E7, "b", "Auto Paint v1"),
    (PAINTABLE, "bRealtimeNetworkSync",             0x013D, "b", "Auto Paint v1"),
]

# 일부러 뺀 것
#
#   URuntimePaintableComponent::MaxDecoySpawnCount  0x01E0 (int32)
#       Net + RepNotify 다. 서버가 런타임에 정상적으로 바꾼다.
#       CDO 와 달라지는 것이 정상이므로 넣으면 전원이 걸린다.
#
#   심재민 담당으로 넘긴 GodMode 상태값 (오프셋만 남겨둔다)
#       ABP_FirstPersonCharacter_Main_C::Dead        0x05AA (bool)
#       ABP_FirstPersonCharacter_Main_C::Invincible  0x05AB (bool)
#       ABP_FirstPersonCharacter_Main_C::Health      0x0638 (double)
#       셋 다 게임 플레이로 변하므로 CDO 대조가 성립하지 않는다.
#       불변식으로 봐야 한다 — 체력이 0 이하인데 Dead 가 false 면 모순이다.

SIZES = {"b": 1, "i": 4, "f": 4, "d": 8}
FMT = {"b": "<?", "i": "<i", "f": "<f", "d": "<d"}

# 부동소수 비교. 설정값은 에디터에서 넣은 상수라 그대로 일치해야 하지만,
# 직렬화 경로에 따라 최하위 비트가 흔들릴 수 있어 여유를 둔다.
EPS = 1e-6


def _read(rt, addr, off, ty):
    raw = rt.pm.read_bytes(addr + off, SIZES[ty])
    return struct.unpack(FMT[ty], raw)[0]


def _differs(a, b, ty):
    if ty in ("d", "f"):
        return abs(a - b) > EPS * max(1.0, abs(b))
    return a != b


def _fmt(v, ty):
    return f"{v:g}" if ty in ("d", "f") else str(v)


def _strip_prefix(name):
    """헤더의 언리얼 타입 접두사(A/U)를 떼어 런타임 이름에 맞춘다."""
    if len(name) > 1 and name[0] in "AU" and name[1].isupper():
        return name[1:]
    return name


def _in_chain(chain, want):
    """상속 사슬에 이 클래스가 있는가. 접두사 차이는 무시한다.

    접두사를 안 맞춰서 대상을 못 찾으면 **에러 없이 검사 0건이 된다.**
    조용한 미탐지라 여기서 관대하게 받는다. 실제로 이 오프셋 표를 처음
    넣었을 때 헤더 이름을 그대로 써서 전 클래스를 놓쳤다.
    """
    w = _strip_prefix(want)
    return any(_strip_prefix(c) == w for c in chain)


# 블루프린트가 들고 있는 컴포넌트 템플릿의 이름 접미사.
# 살아있는 인스턴스는 이것에서 복사되어 만들어진다.
ARCHETYPE_SUFFIX = "_GEN_VARIABLE"


def _build_baselines(rt, rows):
    """클래스 -> 아키타입 주소.

    **대상 클래스의 오브젝트만 넘겨야 한다.** 이름 해석(FName)은 오브젝트당
    비용이 있어서 5만 개를 전부 풀면 11초가 더 걸린다. 실제로 그렇게 짰다가
    3.3초 → 14.6초가 됐다.
    """
    archetypes = {}
    for row in rows:
        if rt.name_of(row).endswith(ARCHETYPE_SUFFIX):
            archetypes.setdefault(row.cls, row.addr)
    return archetypes


def _baseline_of(rt, archetypes, cls_ptr):
    return archetypes.get(cls_ptr) or rt.cdo_of(cls_ptr)


def _is_template(rt, row, archetypes):
    """기준 노릇을 하는 오브젝트인가. 자기 자신과 비교해봐야 의미가 없다."""
    return (row.addr == rt.cdo_of(row.cls)
            or row.addr == archetypes.get(row.cls))


def scan():
    r = DetectorResult("value_tamper")
    t0 = time.time()

    try:
        rt = Runtime()
    except pymem.exception.ProcessNotFound:
        return r.unavailable("게임이 실행 중이 아닙니다")
    except Exception as e:
        return r.fail(f"런타임 초기화 실패: {e}")

    r.meta["target_pid"] = rt.pm.process_id

    try:
        rows = list(rt.iter_objects())
    except Exception as e:
        return r.fail(f"GObjects 순회 실패: {e}")
    r.meta["objects"] = len(rows)

    # 클래스별로 대상 필드를 미리 묶어둔다. 오브젝트마다 전체 목록을 훑으면
    # 5만 개 × 8 필드가 되어 느리다.
    by_class = {}
    for cls, name, off, ty, who in CONFIG_FIELDS:
        by_class.setdefault(cls, []).append((name, off, ty, who))

    checked = 0
    cdo_missing = set()
    # 클래스별로 몇 개를 만났는지 센다. 0 이면 그 클래스는 **검사한 게 아니다.**
    # 이름을 잘못 적으면 에러 없이 검사 0건이 되는데 그게 조용한 미탐지다.
    found = {}
    # 필드 하나가 여러 인스턴스에서 걸릴 수 있다. 걸릴 때마다 add() 하면
    # 같은 설명이 계속 이어붙어 읽을 수 없게 된다. 먼저 모으고 한 번만 낸다.
    violations = {}

    # 대상 클래스에 해당하는 오브젝트만 먼저 추린다. 클래스 사슬은 클래스
    # 포인터 단위로 캐시되므로 5만 개를 훑어도 싸다. 이름 해석은 비싸므로
    # 이 목록에 대해서만 한다.
    # 매칭 결과는 **클래스 단위로 캐시한다.** 오브젝트 5.5만 개에 클래스는
    # 5천여 종뿐이라 오브젝트마다 사슬을 비교하면 같은 일을 열 번씩 한다.
    match_cache = {}
    candidates = []
    for row in rows:
        bases = match_cache.get(row.cls)
        if bases is None:
            chain = rt.class_chain(row.cls)
            bases = [b for b in by_class if _in_chain(chain, b)] if chain else []
            match_cache[row.cls] = bases
        if bases:
            candidates.append((row, bases))
    r.meta["candidates"] = len(candidates)
    r.meta["classes"] = len(match_cache)

    archetypes = _build_baselines(rt, [c[0] for c in candidates])

    # ── 설정값: 아키타입(없으면 CDO) 과 비교 ─────────────────────────
    for row, bases in candidates:
        for base in bases:
            fields = by_class[base]
            if _is_template(rt, row, archetypes):
                continue          # 기준 자신은 비교 대상이 아니다
            ref = _baseline_of(rt, archetypes, row.cls)
            if not ref:
                cdo_missing.add(rt.class_name(row.cls))
                continue
            # **살아있는 인스턴스만 센다.** 파생 블루프린트마다 CDO 가 하나씩
            # 있어서 전체 개수를 세면 검사하지도 않은 것이 커버리지로 잡힌다.
            found[base] = found.get(base, 0) + 1
            for name, off, ty, who in fields:
                try:
                    live = _read(rt, row.addr, off, ty)
                    base_v = _read(rt, ref, off, ty)
                except Exception:
                    continue
                checked += 1
                if _differs(live, base_v, ty):
                    violations.setdefault((name, ty, who), []).append(
                        (rt.name_of(row), live, base_v))

    for (name, ty, who), hits in sorted(violations.items()):
        obj, live, base_v = hits[0]
        n = f" ({len(hits)}개 인스턴스)" if len(hits) > 1 else ""
        r.add(f"config_changed_{name.lower()}", 55,
              f"{name} 이 기본값 {_fmt(base_v, ty)} 에서 {_fmt(live, ty)} 로 "
              f"바뀜{n} — {who} 대상",
              [Evidence("value", f"{name}={_fmt(v, ty)}",
                        f"{o} / 기본값 {_fmt(b, ty)}")
               for o, v, b in hits[:3]])

    r.meta["config_checks"] = checked
    r.meta["fields"] = len(CONFIG_FIELDS)
    r.meta["live_instances"] = {b: found.get(b, 0) for b in by_class}

    missing = [b for b in by_class if not found.get(b)]
    if missing:
        # 클래스는 있는데 **살아있는 인스턴스가 없는** 경우가 대부분이다.
        # (예: 생존자로 플레이하지 않으면 Survivor 인스턴스가 없다)
        # 그 필드들은 검사한 것이 아니므로 커버리지로 밝힌다.
        r.meta["no_live_instance"] = missing
    r.meta["elapsed_ms"] = int((time.time() - t0) * 1000)

    if cdo_missing:
        # CDO 를 못 읽으면 그 클래스는 검사한 게 아니다. 조용히 넘기지 않는다.
        r.meta["cdo_unreadable"] = sorted(cdo_missing)[:5]

    if checked == 0:
        # 대상이 하나도 없으면 검사한 게 아니다. CLEAN 으로 내보내면
        # 로비에서 돌린 세션이 "깨끗함"으로 집계된다.
        return r.fail("대상 클래스를 하나도 찾지 못했습니다: "
                      + ", ".join(by_class)
                      + "\n    게임이 로비이거나 클래스 이름·오프셋이 "
                        "맞지 않습니다.")

    if not r.reasons:
        r.detail = (f"오브젝트 {len(rows):,} / 설정값 비교 {checked}건 "
                    f"— 값 변조 없음")
    return r


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    session = argv[0] if argv else "value_001"
    from result import to_team_event
    res = scan()
    ev = to_team_event(res, session)
    print(json.dumps(ev, ensure_ascii=False, indent=2))
    return {"NORMAL": 0, "ERROR": 2, "OFFLINE": 2}.get(ev["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
