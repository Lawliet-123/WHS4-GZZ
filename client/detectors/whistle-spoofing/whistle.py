"""휘파람(Provocation) 조작 전용 탐지기

담당: 휘파람 관련 조작만. 범용 후킹 탐지는 `detector.py` 가 맡는다.

`detector.py` 와의 차이는 **귀속**이다. 범용 탐지기는 "무언가가 후킹했다"까지
말하지만, 이 탐지기는 FName 해석으로 **"어떤 함수가, 어떤 클래스에서"** 까지
말한다. 운영자가 조치하려면 그 이름이 있어야 한다.

## 검사 항목

  W-1  도발 경로 UFunction 의 ExecFunction 교체   ← 함수 이름 특정
  W-2  캐릭터 클래스의 ProcessEvent vtable 변조   ← 클래스 이름 특정
  W-3  도발 사운드 자산 교체 (로컬 소리 변조)      ← 사운드 이름 특정

## W-0. 원리적으로 못 잡는 것 (먼저 적는다)

우리가 실측한 취약점 6가지(docs/19) 중 **이 탐지기가 막는 것은 없다.**

  - V1 호출 경로 / V2 빈도 / V3 역할 / V4 생존상태 / V5 대상지정
      → 전부 RPC **호출 시점**에 드러난다. 그 순간을 외부에서 관측하려면
        우리가 ProcessEvent 를 후킹해야 하는데, 그건 탐지 대상과 같은 기법이다.
      → 후킹을 안 쓰고 RPC 를 직접 호출만 해도 V1~V5 는 그대로 뚫린다.
        즉 후킹 탐지는 **한 가지 구현 방식**을 잡는 것이지 취약점을 막는 게 아니다.
  - V6 위치 노출 → 복제된 데이터를 읽는 것이라 흔적이 없다.

  이 여섯 개는 서버측에서만 닫힌다. 권고안: `docs/23-whistle-server-side-fix.md`
"""

import json
import struct
import sys
import time

import pymem.exception

# core/ 는 2번 모듈(LocalGuard/memory_integrity) 이 갖고 있다. 이 파일은
# 휘파람 핵 담당(TelemetryServer) 쪽이라 부모 폴더에 core/ 가 없다.
# 레포 루트를 거쳐 한 번 건너간다. 직접 실행해도 러너로 돌려도 둘 다 된다.
#
# core/ 를 공용 shared/ 로 올릴지는 8번(공통 로그 규격) 확정 후에 정한다.
# 지금 올리면 아직 주인이 없는 자리를 선점하게 된다.
import os as _os, sys as _sys

def _memory_integrity():
    """2번 모듈 폴더를 찾는다. **상위 폴더 개수를 세지 않는다.**

    이 파일은 detectors/ 가 생기면서 한 번 더 내려갔다. 그때 dirname 을
    세 번 부르던 코드가 조용히 엉뚱한 폴더를 가리켰다. 폴더가 또 움직여도
    안 깨지게 올라가면서 찾는다.
    """
    d = _os.path.dirname(_os.path.abspath(__file__))
    while True:
        cand = _os.path.join(d, "LocalGuard", "memory_integrity")
        if _os.path.isdir(cand):
            return cand
        parent = _os.path.dirname(d)
        if parent == d:
            raise RuntimeError(
                "LocalGuard/memory_integrity 를 찾지 못했습니다. "
                "레포 안에서 실행하고 있는지 확인해 주세요.")
        d = parent


_sys.path.insert(0, _memory_integrity())

from core.result import DetectorResult, Evidence
from core.unreal import Runtime, PROCESS_EVENT_IDX

# 게임 애셋에 오타가 있다. Provocation 이 아니라 **Provoaction** 이다.
# 이 오타를 모르면 영어 키워드 검색으로는 영원히 안 나온다.
PROVO_TOKENS = ("provocation", "provoaction")

CHARACTER_AUDIO_OFF = 0x0B40   # 캐릭터의 UAudioComponent*
AUDIO_SOUND_OFF = 0x03F8       # UAudioComponent::Sound

UFUNCTION_CLASSES = {"Function", "DelegateFunction", "SparseDelegateFunction"}


def _is_provo(name):
    low = name.lower()
    return any(t in low for t in PROVO_TOKENS)


def scan():
    r = DetectorResult("whistle")
    t0 = time.time()

    try:
        rt = Runtime()
    except pymem.exception.ProcessNotFound:
        # 게임이 안 켜진 것과 오프셋이 틀린 것은 둘 다 ERROR 지만 원인이 다르다.
        # 전자는 측정을 다시 하면 되고, 후자는 우리가 고쳐야 할 결함이다.
        return r.unavailable("게임이 실행 중이 아닙니다")
    except Exception as e:
        # 오프셋 불일치를 CLEAN 으로 뭉개지 않는다. 그게 조용한 미탐지의 원인이다.
        return r.fail(f"런타임 초기화 실패: {e}")

    r.meta["target_pid"] = rt.pm.process_id

    try:
        rows = list(rt.iter_objects())
    except Exception as e:
        return r.fail(f"GObjects 순회 실패: {e}")
    r.meta["objects"] = len(rows)

    # ── W-1. ExecFunction 교체 ───────────────────────────────────────────
    # 이름 해석이 되므로 UFunction 을 클래스 이름으로 정확히 고른다.
    # detector.py 의 다수결 휴리스틱이 필요 없다.
    fns = [x for x in rows if x.exec_fn and rt.class_name(x.cls) in UFUNCTION_CLASSES]
    r.meta["ufunctions"] = len(fns)

    self_hooks = []
    for x in fns:
        if rt.in_game_module(x.exec_fn):
            continue
        fname = rt.name_of(x)
        owner = rt.owner_of(x.exec_fn)
        if rt.is_self_module(x.exec_fn):
            # **우리 자신의 관측용 후크다.** ac_whistle DLL 은 도발 RPC 를 보려고
            # ExecFunction 을 바꾼다. 이걸 핵으로 세면 후크를 넣은 세션이 전부
            # DETECTED 가 되어, 정작 핵이 있는지 없는지를 구분할 수 없다.
            # 점수만 빼고 근거에는 남긴다 — 안 남기면 나중에 "왜 이 세션만
            # 다르지"를 설명할 수 없다. core/selfid.py 참고.
            self_hooks.append(f"{fname} -> {owner}")
            continue
        # 도발 경로거나 오디오 재생 경로면 휘파람 핵으로 귀속한다
        related = _is_provo(fname) or fname.lower() in ("play", "playsound", "setsound")
        r.add("exec_function_hooked", 60 if related else 40,
              f"{fname}() 의 ExecFunction 이 {owner} 로 교체됨",
              [Evidence("address", f"0x{x.exec_fn:016X}", f"{fname} -> {owner}")])

    if self_hooks:
        r.meta["self_hooks"] = self_hooks
        r.evidence.append(Evidence(
            "module", f"안티치트 자체 후크 {len(self_hooks)}건",
            "관측용 ac_whistle DLL — 점수에서 제외"))

    # ── W-2. 캐릭터 vtable 변조 + W-3. 사운드 교체 ───────────────────────
    chars = [x for x in rows if "cleon_character" in rt.class_name(x.cls).lower()]
    r.meta["characters"] = len(chars)

    seen_vt = set()
    for x in chars:
        cname = rt.class_name(x.cls)

        if x.vtable not in seen_vt:
            seen_vt.add(x.vtable)
            fn = rt.rq(x.vtable + PROCESS_EVENT_IDX * 8)
            if fn and not rt.in_game_module(fn):
                r.add("character_vtable_hooked", 60,
                      f"{cname} 의 ProcessEvent 가 {rt.owner_of(fn)} 로 교체됨",
                      [Evidence("address", f"0x{fn:016X}", cname)])

        audio = rt.rq(x.addr + CHARACTER_AUDIO_OFF)
        if not audio:
            continue
        snd = rt.rq(audio + AUDIO_SOUND_OFF)
        if not snd:
            continue
        sname = rt.object_name(snd)
        if sname and not _is_provo(sname):
            # 도발용 컴포넌트가 도발이 아닌 사운드를 들고 있다.
            # 이 컴포넌트가 다른 용도로도 쓰일 수 있어 단독 근거로는 약하다.
            r.add("provocation_sound_swapped", 35,
                  f"{cname} 의 도발 오디오가 '{sname}' 를 들고 있음 (기대: SC_Provoaction)",
                  [Evidence("value", sname, f"{cname} +0x{CHARACTER_AUDIO_OFF:X}")])

    r.meta["elapsed_ms"] = int((time.time() - t0) * 1000)
    if not r.reasons:
        r.detail = (f"오브젝트 {len(rows):,} / UFunction {len(fns):,} / "
                    f"캐릭터 {len(chars)} — 휘파람 관련 변조 없음")
    return r


def main():
    res = scan()
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    return 0 if res.result == "CLEAN" else 1


if __name__ == "__main__":
    sys.exit(main())
