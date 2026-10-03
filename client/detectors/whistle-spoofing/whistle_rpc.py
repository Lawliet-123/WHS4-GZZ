"""후크가 남긴 RPC 위반 기록을 팀 공통 계약으로 바꾼다.

판정은 C++ 후크(`native/src/main.cpp`)가 인프로세스에서 한다.
RPC 호출 시점은 외부에서 볼 수 없어서 후킹이 아니면 방법이 없다.
이 파일은 그 결과를 읽어 `result.py` 계약으로 옮기는 보고 계층이다.

팀이 파이썬으로 통일했으므로 **다른 탐지기와 같은 형식으로 나온다.**
후크가 C++ 인 것은 구현 제약이지 계약의 예외가 아니다.

사용법:
    python rpc_report.py                      # 기본 경로에서 읽는다
    python rpc_report.py <ac-whistle.jsonl>
"""

import ctypes
import json
import os
import sys
import time

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

LOG_NAME = "ac-whistle.jsonl"

# 후크는 **자기 DLL 이 있는 폴더**에 로그를 쓴다(main.cpp `DllDirectory()`).
# 빌드 위치가 사람마다 다르고 개발 트리와 팀 레포의 배치도 달라서,
# 있을 법한 자리를 순서대로 본다. 인자로 직접 넘겨도 된다.
#
# **이 목록이 한 자리만 보게 두면 안 된다.** 실제로 개발 트리 기준 경로
# 하나만 박아뒀다가, 팀 레포에서는 후크가 로그를 정상적으로 남겼는데도
# "로그가 없습니다"로 ERROR 가 났다.
# **_HERE 기준이다.** 예전에 _ROOT(부모)를 썼는데, 이 파일이
# TelemetryServer/whistle-spoofing/ 으로 내려오면서 부모가 8개 핵이 공유하는
# TelemetryServer/ 가 됐다. 그대로 뒀으면 남의 폴더를 뒤지고 로그는 못 찾는다.
_HERE = os.path.dirname(os.path.abspath(__file__))

LOG_CANDIDATES = [
    os.path.join(_HERE, "logs", "raw", LOG_NAME),
    os.path.join(_HERE, "native", "whistle_hook", "bin", "Release", LOG_NAME),
    os.path.join(_HERE, "native", "whistle_hook", "bin", LOG_NAME),
    # 개발 트리 배치. 지우지 않는다 — 후보를 줄였다가 후크가 로그를 남겼는데도
    # ERROR 가 난 적이 있다.
    os.path.join(_HERE, "native", "bin", "Release", LOG_NAME),
    os.path.join(_HERE, "native", "bin", LOG_NAME),
]


def default_log():
    """존재하는 첫 후보. 하나도 없으면 첫 후보(오류 메시지에 쓴다)."""
    for p in LOG_CANDIDATES:
        if os.path.exists(p):
            return p
    return LOG_CANDIDATES[0]

# 후크가 내보내는 위반 코드 -> (점수, 대응하는 실측 취약점, 서버측 권고)
RULES = {
    "role_violation":     (60, "V3 호출자 역할", "S-1"),
    "dead_caller":        (60, "V4 생존 상태", "S-1"),
    "foreign_target":     (60, "V5 대상 지정", "S-2"),
    "cooldown_violation": (40, "V2 호출 빈도", "S-3"),
    "no_input_event":     (40, "V1 호출 경로", "S-1"),
}


def scan(path=None):
    if _WATCH is not None and (path is None or path == _WATCH["path"]):
        return _scan_window(_WATCH)
    r = DetectorResult("whistle_rpc")
    path = path or default_log()

    if not os.path.exists(path):
        # 어디를 봤는지 전부 적는다. 경로가 틀린 것과 후크가 안 붙은 것은
        # 원인이 완전히 다른데, 한 자리만 보여주면 구분이 안 된다.
        return r.fail("후크 로그가 없습니다. 찾아본 자리:\n    "
                      + "\n    ".join(LOG_CANDIDATES)
                      + "\n    ac_whistle_v1.dll 을 주입했는지 확인하세요.")

    started = False
    violations = []
    hooks_total = 0
    calls_seen = 0
    pe_seen = 0
    exec_hooks = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                kind = ev.get("event")
                if kind == "start":
                    started = True
                elif kind == "hooks":
                    hooks_total = ev.get("total", hooks_total)
                elif kind == "exec_hook":
                    exec_hooks.append(ev.get("fn", "?"))
                elif kind == "stats":
                    calls_seen = max(calls_seen, ev.get("calls", 0))
                    pe_seen = max(pe_seen, ev.get("process_event", 0))
                elif ev.get("codes"):
                    violations.append(ev)
    except Exception as e:
        return r.fail(f"로그를 읽지 못했습니다: {e}")

    if not started:
        # 후크가 붙지 못한 것과 "위반이 없는 것"은 다르다.
        # 이걸 CLEAN 으로 뭉개면 조용한 미탐지가 된다.
        return r.fail("후크 시작 기록이 없습니다. DLL 이 로드되지 않았습니다.")

    r.meta["log"] = path
    r.meta["hooked_vtables"] = hooks_total
    r.meta["provocation_calls"] = calls_seen
    r.meta["process_event_calls"] = pe_seen
    r.meta["exec_hooked_functions"] = exec_hooks
    r.meta["violation_records"] = len(violations)

    if calls_seen == 0:
        # **위반이 없는 것과 볼 것이 없었던 것은 다르다.**
        # 후크가 붙기만 하고 도발이 한 번도 안 불렸으면 이 검사는 아무것도
        # 검증하지 않은 것이다. 후크가 고장나도 로그 모양이 똑같으므로
        # CLEAN 으로 내보내면 조용한 미탐지가 된다.
        if not exec_hooks:
            hint = ("도발 UFunction 의 ExecFunction 을 하나도 걸지 못했습니다. "
                    "게임이 로비이거나 함수 이름이 다릅니다.")
        elif pe_seen == 0:
            hint = ("후크가 한 번도 불리지 않았습니다. "
                    "vtable 슬롯 교체가 실제로 먹혔는지 확인하세요.")
        else:
            hint = (f"후크는 불리고 있습니다(ProcessEvent {pe_seen:,}건). "
                    f"ExecFunction 을 {len(exec_hooks)}개 걸었으므로 "
                    f"휘파람을 불지 않은 것으로 보입니다.")
        return r.fail(
            f"후크는 붙었으나(vtable {hooks_total}개) 도발 호출을 "
            f"한 건도 관측하지 못했습니다.\n    {hint}")

    if not violations:
        r.detail = (f"vtable {hooks_total}개 + ExecFunction {len(exec_hooks)}개 후킹 / "
                    f"도발 호출 {calls_seen}건 관측 — 위반 없음")
        return r

    return _score(r, violations)


def _score(r, violations):
    """위반 기록을 점수와 근거로 바꾼다. 단발·구간 관측이 같이 쓴다."""
    # 같은 코드가 여러 번 나와도 점수는 한 번만 준다.
    # 반복은 확신을 높이지만 등급을 무한히 올리면 의미가 없어진다.
    counts = {}
    for ev in violations:
        for code in ev.get("codes", []):
            counts[code] = counts.get(code, 0) + 1

    for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        points, vuln, fix = RULES.get(code, (30, "미분류", "-"))
        r.add(code, points,
              f"{vuln} 위반 {n}회 (서버측 권고 {fix})",
              [Evidence("value", f"{n}회", vuln)])

    # 근거로 실제 로그 몇 줄을 같이 넘긴다. 운영자가 조치하려면 필요하다.
    for ev in violations[:3]:
        r.evidence.append(Evidence(
            "value", f"t={ev.get('t')}s {ev.get('fn')}",
            ev.get("detail", "")))

    blocked = sum(1 for ev in violations if ev.get("blocked"))
    if blocked:
        r.meta["blocked"] = blocked
        r.detail += f" / 차단 {blocked}건"
    return r


# ═══════════════════════════════════════════════════════════════════════
# 반복 관측 (run_session.py --watch)
# ═══════════════════════════════════════════════════════════════════════
#
# 단발 scan() 은 후크 로그를 **처음부터 끝까지** 읽는다. 한 번 보고 끝나는
# 검사에서는 맞는 동작이다. 반복 관측에서는 틀린다 — 위반이 한 번 찍히면
# 그 뒤 모든 바퀴가 계속 DETECTED 가 되어 핵을 꺼도 안 내려오고(Post-OFF 가
# 세션 끝까지), 이전 게임 실행의 위반까지 섞인다. 후크는 로그를 덮어쓰지 않고
# 이어 쓰기 때문이다(main.cpp LogLine, OPEN_ALWAYS + 끝에 붙이기).
# 2026-09-23 검토에서 재현됐다.
#
# 그래서 반복 관측에서는 **바퀴마다 새로 쓰인 줄만** 읽는다.
# begin_watch() 가 지금 파일 끝을 기준점으로 잡고, 이후 scan() 은 거기서부터
# 읽고 기준점을 옮긴다. 이전 실행의 위반은 기준점 앞이라 안 섞인다.
#
# 후크 연결 상태(start / hooks / exec_hook)는 기준점 **앞**에 있으므로
# 시작할 때 한 번 전부 읽어 따로 들고 있는다. 이것까지 버리면 후크가 붙어
# 있는데도 "시작 기록이 없다" 로 ERROR 가 난다.
#
# 호출 수는 후크가 3초마다 남기는 누적 카운터(stats.calls)의 **증가분**이다.
# 바퀴 사이에 stats 줄이 안 찍혔으면 0 으로 보이고 다음 바퀴에 몰려서 잡힌다.
# 최대 3초 늦을 수 있다는 뜻이다.

_WATCH = None
_STATE_FILE = None

# ── 후크가 **지금 게임에** 붙어 있는가 ──────────────────────────────────
#
# 후크 연결 상태(start)는 기준점 앞에서 한 번 읽어 들고 있는데, 로그는 게임을
# 다시 켜도 이어 쓰인다. 그래서 **지난 게임의 start 줄만으로 "붙었다" 가 됐다.**
# 이번 게임에 후크를 안 넣었어도 바퀴마다 NORMAL "이 구간 도발 호출 없음" 이
# 나왔다 — 조용한 미탐지(10/1 재현: 게임 꺼짐, 9/27 로그로 NORMAL 2바퀴).
#
# 후크는 살아 있는 동안 ProcessEvent 를 지날 때마다 3초 간격으로 stats 를 남긴다
# (main.cpp ReportStats, kStatsIntervalMs). 그래서 로그가
#   - 지금 게임이 켜지기 **전에** 마지막으로 쓰였거나
#   - HOOK_STALE_S 넘게 안 바뀌었으면
# 이번 게임에는 후크가 없거나 멈춘 것이다. 단발 scan() 은 저장된 로그를 나중에
# 분석하는 용도라 이 검사를 하지 않는다.
HOOK_STALE_S = 15.0
GAME_EXE = "PenguinHotel-Win64-Shipping.exe"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _game_started_at(exe=GAME_EXE):
    """게임 프로세스가 켜진 시각(epoch 초). 게임이 없거나 못 읽으면 None.

    조회 권한(0x1000)만 쓴다. 메모리 읽기도 안 하니 1번에 잡힐 일이 없다.
    """
    try:
        import pymem.process
        entry = pymem.process.process_from_name(exe, exact_match=True)
    except Exception:
        return None
    if not entry:
        return None
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 4
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, entry.th32ProcessID)
    if not h:
        return None
    try:
        t = [ctypes.c_ulonglong() for _ in range(4)]
        if not k32.GetProcessTimes(h, *[ctypes.byref(x) for x in t]):
            return None
        return t[0].value / 1e7 - 11644473600      # FILETIME(1601년 기준 100ns) -> epoch
    finally:
        k32.CloseHandle(h)


def _hook_state(path, now=None):
    """후크가 지금 게임에 살아 있는가. (사유 종류, 설명), 살아 있으면 (None, None).

    종류
      before_game  로그가 지금 게임이 켜지기 전에 마지막으로 쓰였다 — 새 줄도 전부 그 전 것
      stale        HOOK_STALE_S 넘게 안 바뀌었다 — 후크가 멈췄거나 게임이 꺼졌다
    """
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None, None       # 파일 없음은 호출부가 따로 알린다
    now = time.time() if now is None else now
    game = _game_started_at()
    if game is not None and mtime < game:
        return "before_game", (f"후크 로그가 지금 게임이 켜지기 전({game - mtime:,.0f}초 전)에 "
                               f"마지막으로 쓰였습니다. 이번 게임에는 후크가 없습니다.")
    age = now - mtime
    if age > HOOK_STALE_S:
        return "stale", (f"후크 로그가 {age:,.0f}초째 갱신되지 않았습니다. 후크는 살아 있으면 "
                         f"3초마다 기록합니다 — 이번 게임에 후크가 없거나 멈췄습니다.")
    return None, None


def _hook_not_live(path, now=None):
    """후크가 지금 게임에 살아 있지 않으면 그 이유, 살아 있으면 None."""
    return _hook_state(path, now)[1]


def begin_watch(path=None, state_file=None, t0=None):
    """반복 관측을 시작한다. 지금 로그 끝을 기준점으로 잡는다.

    `state_file` 을 주면 기준점을 그 파일에 저장하고, 다음에 같은 파일로 시작할 때
    이어서 읽는다. **런처가 30초마다 새 프로세스로 부르기 때문에 필요하다.**
    메모리에만 두면 실행마다 기준점이 사라져서, 프로세스가 바뀔 때마다 로그를
    처음부터 다시 읽는다. 그러면 위반이 한 번 찍힌 뒤 모든 바퀴가 DETECTED 가 된다.

    상태 파일은 세션 이름을 따라간다. 그런데 런처 `--overwrite` 로 **같은 이름을 다시
    쓰면** 지난 실행의 상태 파일이 남아, 그 뒤 쌓인 지난 게임 위반을 새 세션 첫 검사가
    읽었다(10/3 검토에서 재현). 그래서 `t0`(세션 기준 시각, 런처 실행마다 다름)를 같이
    저장하고 **다르면 이어받지 않는다.**
    """
    global _WATCH, _STATE_FILE
    _STATE_FILE = state_file
    st = None
    if state_file and os.path.exists(state_file):
        try:
            with open(state_file, encoding="utf-8") as f:
                saved = json.load(f)
            # 로그 파일이 바뀌었거나 다른 실행의 기준점이면 이어받지 않는다.
            if (isinstance(saved, dict) and saved.get("path") == (path or default_log())
                    and (t0 is None or saved.get("t0") == t0)):
                st = saved
                st.setdefault("exec_hooks", [])
        except Exception:
            st = None
    if st is None:
        st = {"path": path or default_log(), "offset": 0, "started": False,
              "hooks_total": 0, "exec_hooks": [], "pe": 0, "calls": 0,
              "restarts": 0, "t0": t0}
        if os.path.exists(st["path"]):
            _consume(st)        # 후크 상태만 챙기고 기존 위반은 버린다
    _WATCH = st
    _save_state()
    return st


def end_watch():
    global _WATCH
    _save_state()
    _WATCH = None


def _save_state():
    if not (_STATE_FILE and _WATCH):
        return
    try:
        os.makedirs(os.path.dirname(_STATE_FILE) or ".", exist_ok=True)
        tmp = f"{_STATE_FILE}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_WATCH, f, ensure_ascii=False)
        os.replace(tmp, _STATE_FILE)
    except Exception:
        # 기준점을 못 남겨도 이번 바퀴 결과는 유효하다. 다음 실행이 처음부터 읽을 뿐이다.
        pass


def _consume(st):
    """기준점부터 끝까지 읽어 후크 상태를 갱신한다. (새 위반, 새 호출 수) 를 돌려준다."""
    size = os.path.getsize(st["path"])
    if size < st["offset"]:
        # 파일이 줄었다 = 로그를 지우고 다시 주입했다. 처음부터 다시 읽는다.
        st["offset"] = 0
        st["calls"] = 0
        st["restarts"] += 1
    with open(st["path"], "rb") as f:
        f.seek(st["offset"])
        data = f.read()
    # 마지막 줄은 후크가 쓰는 중일 수 있다. 개행으로 안 끝난 꼬리는 다음 바퀴로 미룬다.
    cut = data.rfind(b"\n") + 1
    st["offset"] += cut

    violations, calls_before = [], st["calls"]
    for line in data[:cut].decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        kind = ev.get("event")
        if kind == "start":
            # 이 사이에 다시 주입됐다. 누적 카운터가 0 부터 다시 센다.
            st["started"] = True
            st["calls"] = 0
            calls_before = 0
        elif kind == "hooks":
            st["hooks_total"] = ev.get("total", st["hooks_total"])
        elif kind == "exec_hook":
            st["exec_hooks"].append(ev.get("fn", "?"))
        elif kind == "stats":
            st["calls"] = max(st["calls"], ev.get("calls", 0))
            st["pe"] = max(st["pe"], ev.get("process_event", 0))
        elif ev.get("codes"):
            violations.append(ev)
    return violations, max(0, st["calls"] - calls_before)


def _scan_window(st):
    r = DetectorResult("whistle_rpc")
    if not os.path.exists(st["path"]):
        return r.fail("후크 로그가 없습니다. 찾아본 자리:\n    "
                      + "\n    ".join(LOG_CANDIDATES)
                      + "\n    ac_whistle DLL 을 주입했는지 확인하세요.")
    try:
        violations, calls = _consume(st)
    except Exception as e:
        return r.fail(f"로그를 읽지 못했습니다: {e}")

    if not st["started"]:
        return r.fail("후크 시작 기록이 없습니다. DLL 이 로드되지 않았습니다.")
    # start 줄은 지난 게임 것일 수 있다. 지금 살아 있는지 따로 본다(위 HOOK_STALE_S).
    #
    # 다만 후크가 **멈춘(stale)** 경우, 기준점 뒤에 새로 쓰인 위반은 버리지 않는다.
    # 기준점은 이 세션의 첫 검사 때 로그 끝에서 잡으므로(begin_watch, 세션 시각이 다르면
    # 이어받지 않음) 그 뒤 줄은 이번 세션 것이고, 이미 읽어 기준점을 옮겼으니 여기서
    # 버리면 다시는 못 본다. 게임이 꺼진 직후 런처가 돌리는 마지막 검사가 이 경우다
    # (Module.final_run) — 위반 뒤 후크가 멈춰 15초가 지났을 수 있다.
    # 로그가 **지금 게임 전에** 쓰였으면(before_game) 새 줄도 전부 지난 게임 것이라
    # 구제하지 않는다. 위반이 없을 때도 ERROR 다 — 멈춘 후크의 조용함을 정상으로 안 읽는다.
    kind, dead = _hook_state(st["path"])
    if dead and (kind == "before_game" or not violations):
        return r.fail(dead + "\n    ac_whistle DLL 을 이번 게임에 주입했는지 확인하세요.")
    if not st["exec_hooks"]:
        return r.fail("도발 UFunction 의 ExecFunction 을 하나도 걸지 못했습니다. "
                      "게임이 로비이거나 함수 이름이 다릅니다.")

    r.meta["mode"] = "window"
    r.meta["log"] = st["path"]
    r.meta["window_calls"] = calls
    r.meta["window_violation_records"] = len(violations)
    r.meta["cumulative_calls"] = st["calls"]
    r.meta["hooked_vtables"] = st["hooks_total"]
    if st["restarts"]:
        r.meta["log_restarts"] = st["restarts"]
    if dead:
        # 위반은 점수로 내되, 지금은 후크가 멈췄다는 것도 같이 남긴다.
        r.meta["hook_live"] = False
        r.meta["hook_note"] = dead

    if not violations:
        # 단발 모드와 달리 **호출 0건이 ERROR 가 아니다.** 이 구간에 휘파람을
        # 안 불었을 뿐이고, 후크가 붙어 있다는 건 위에서 이미 확인했다.
        # 호출 수를 meta 에 같이 남겨 "안 불어서 0" 과 "불었는데 정상" 을 가른다.
        r.detail = (f"이 구간 도발 호출 {calls}건 — 위반 없음" if calls
                    else "이 구간 도발 호출 없음")
        return r
    return _score(r, violations)


def main(argv=None):
    """단독 실행. 인자로 session_id 를 주면 그대로 쓴다 (측정 실험용)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    session = argv.pop(0) if argv and not argv[0].endswith(".jsonl") else "whistle_rpc_001"
    from core.result import to_team_event
    res = scan(argv[0] if argv else None)
    ev = to_team_event(res, session)
    print(json.dumps(ev, ensure_ascii=False, indent=2))
    return {"NORMAL": 0, "ERROR": 2, "OFFLINE": 2}.get(ev["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
