"""값 변조 탐지 — 역할표 2번의 B축

2번은 두 축이다. A축(코드 변조·후킹)은 `whistle_detector.py` 와 `detector.py` 가
맡는다. 이 파일이 나머지 절반, **"기존 치트의 대상 값 감시"** 다.

A축은 후킹 여부만 보므로 핵 종류를 가리지 않았다. B축은 반대다.
핵마다 건드리는 필드가 다르므로 **대상을 하나씩 알아야 한다.**
대상 목록은 심재민이 2026-09-15 정리한 "ZIP 실제 코드 기준" 표에서 가져왔고,
오프셋은 Dumper-7 CppSDK 에서 직접 확인했다.

## 기준값을 저장하지 않는다

값 변조를 잡으려면 "원래 얼마였나"를 알아야 한다. 상수로 박아두면 게임이
패치될 때마다 틀리고, 틀린 줄도 모른 채 통과한다 — 조용한 미탐지다.

그래서 **CDO(ClassDefaultObject)를 기준으로 쓴다.** 언리얼은 클래스마다
기본값 인스턴스를 들고 있다. 살아있는 인스턴스와 그것을 비교한다.
A축이 "게임 모듈 안이냐 밖이냐"만 보고 정상 값을 저장하지 않았던 것과 같다.

## 다만 CDO 비교가 성립하지 않는 필드가 있다

체력은 게임 중에 당연히 변한다. CDO 와 다르다고 잡으면 전원이 걸린다.
그래서 필드를 두 종류로 나눈다.

    설정값   런타임에 바뀌면 안 되는 값      → CDO 와 다르면 변조
    상태값   게임 플레이로 바뀌는 값          → CDO 비교 불가. 불변식으로 본다

상태값은 "서로 모순되는가"를 본다. 죽지 않았는데 체력이 0 이하이거나,
체력이 기본값보다 크면 게임 로직으로는 나올 수 없는 조합이다.

## 못 잡는 것

  - **에임봇** `AController::ControlRotation` 은 매 프레임 정상적으로 바뀐다.
    정적 스냅샷 한 장으로는 정상과 구분할 수 없다. Raw Input 과 비교해야 하고
    그건 3번(입력·시그니처) 담당이다.
  - **ESP** 는 읽기만 해서 바꾸는 값이 없다. B축에 걸릴 것이 없다.
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

CHARACTER = "ABP_FirstPersonCharacter_Main_C"
SURVIVOR = "ABP_FirstPersonCharacter_cLeon_Character_Survivor_C"
NEAR_INTERACT = "UBPC_NearInteract_C"

CONFIG_FIELDS = [
    # 런타임에 바뀌면 안 되는 값. CDO 와 다르면 변조다.
    (CHARACTER,     "InteractLength", 0x0510, "d", "Hide Anywhere"),
    (CHARACTER,     "EnableInteract", 0x0676, "b", "Hide Anywhere"),
    (SURVIVOR,      "PreStencil",     0x0D20, "i", "Hide Anywhere"),
    (NEAR_INTERACT, "SearchRadius",   0x00C0, "d", "Hide Anywhere"),
    (NEAR_INTERACT, "Angle",          0x00C8, "d", "Hide Anywhere"),
]

STATE_FIELDS = [
    # 게임 플레이로 바뀌는 값. 읽어서 불변식 검사에만 쓴다.
    (CHARACTER, "Dead",       0x05AA, "b"),
    (CHARACTER, "Invincible", 0x05AB, "b"),
    (CHARACTER, "Health",     0x0638, "d"),
]

SIZES = {"b": 1, "i": 4, "d": 8}
FMT = {"b": "<?", "i": "<i", "d": "<d"}

# 부동소수 비교. 설정값은 에디터에서 넣은 상수라 그대로 일치해야 하지만,
# 직렬화 경로에 따라 최하위 비트가 흔들릴 수 있어 여유를 둔다.
EPS = 1e-6


def _read(rt, addr, off, ty):
    raw = rt.pm.read_bytes(addr + off, SIZES[ty])
    return struct.unpack(FMT[ty], raw)[0]


def _differs(a, b, ty):
    if ty == "d":
        return abs(a - b) > EPS * max(1.0, abs(b))
    return a != b


def _fmt(v, ty):
    return f"{v:g}" if ty == "d" else str(v)


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
    # 필드 하나가 여러 인스턴스에서 걸릴 수 있다. 걸릴 때마다 add() 하면
    # 같은 설명이 계속 이어붙어 읽을 수 없게 된다. 먼저 모으고 한 번만 낸다.
    violations = {}

    # ── 설정값: CDO 와 비교 ──────────────────────────────────────────
    for row in rows:
        chain = rt.class_chain(row.cls)
        if not chain:
            continue
        for base, fields in by_class.items():
            if base not in chain:
                continue
            cdo = rt.cdo_of(row.cls)
            if not cdo:
                cdo_missing.add(chain[0])
                continue
            if row.addr == cdo:
                continue          # CDO 자기 자신은 비교 대상이 아니다
            for name, off, ty, who in fields:
                try:
                    live = _read(rt, row.addr, off, ty)
                    base_v = _read(rt, cdo, off, ty)
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

    # ── 상태값: 불변식 ──────────────────────────────────────────────
    invincible = []
    contradictions = []
    over_max = []
    chars = 0

    for row in rows:
        if CHARACTER not in rt.class_chain(row.cls):
            continue
        cdo = rt.cdo_of(row.cls)
        if row.addr == cdo:
            continue
        chars += 1
        try:
            dead = _read(rt, row.addr, 0x05AA, "b")
            inv = _read(rt, row.addr, 0x05AB, "b")
            hp = _read(rt, row.addr, 0x0638, "d")
        except Exception:
            continue
        obj = rt.name_of(row)

        if inv:
            invincible.append((obj, hp))
        if not dead and hp <= 0.0:
            contradictions.append((obj, hp))
        if cdo:
            try:
                base_hp = _read(rt, cdo, 0x0638, "d")
                if base_hp > 0 and hp > base_hp + EPS:
                    over_max.append((obj, hp, base_hp))
            except Exception:
                pass

    if contradictions:
        # 게임 로직으로는 나올 수 없는 조합이다. Dead 를 강제로 false 로
        # 돌려놓는 갓모드가 정확히 이 모양을 만든다.
        r.add("dead_flag_contradiction", 70,
              f"체력이 0 이하인데 사망 상태가 아님 {len(contradictions)}건",
              [Evidence("value", f"Health={hp:g}", obj)
               for obj, hp in contradictions[:3]])

    if over_max:
        r.add("health_over_default", 60,
              f"체력이 기본값을 초과 {len(over_max)}건",
              [Evidence("value", f"Health={hp:g}", f"{obj} / 기본 {b:g}")
               for obj, hp, b in over_max[:3]])

    if invincible:
        # 게임이 정상적으로 무적을 켜는 구간(리스폰 직후 등)이 있을 수 있다.
        # 단독으로는 확정하지 않는다. 확인 전까지 의심까지만 준다.
        r.add("invincible_flag_set", 35,
              f"Invincible 플래그가 켜진 캐릭터 {len(invincible)}명 "
              f"(정상 무적 구간 여부는 미확인)",
              [Evidence("value", "Invincible=True", obj)
               for obj, _ in invincible[:3]])

    r.meta["characters"] = chars
    r.meta["config_checks"] = checked
    r.meta["elapsed_ms"] = int((time.time() - t0) * 1000)

    if cdo_missing:
        # CDO 를 못 읽으면 그 클래스는 검사한 게 아니다. 조용히 넘기지 않는다.
        r.meta["cdo_unreadable"] = sorted(cdo_missing)[:5]

    if checked == 0 and chars == 0:
        return r.fail("대상 클래스를 하나도 찾지 못했습니다. "
                      "게임이 로비이거나 오프셋이 맞지 않습니다.")

    if not r.reasons:
        r.detail = (f"오브젝트 {len(rows):,} / 캐릭터 {chars} / "
                    f"설정값 비교 {checked}건 — 값 변조 없음")
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
