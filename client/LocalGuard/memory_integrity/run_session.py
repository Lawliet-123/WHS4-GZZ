"""안티치트 실행기 — 탐지기를 한 세션으로 묶어 팀 공통 형식으로 내보낸다.

역할표 6번(TelemetryServer)의 클라이언트 쪽. 탐지 자체는 각 탐지기가 하고,
이 파일은 **한 번의 검사를 하나의 세션으로 묶어 같은 형식으로 내보내는 일**만 한다.

## 왜 따로 필요한가

탐지기를 각자 돌리면 세 가지가 깨진다.

  1. `session_id` 가 제각각이라 "한 번의 테스트"로 묶이지 않는다
  2. `timestamp_ms` 기준 시각(t0)이 모듈마다 달라 ReplayAnalyzer 에서
     타임라인이 안 겹친다
  3. 탐지기 하나가 예외로 죽으면 나머지 결과까지 날아간다

세 번째가 제일 위험하다. **검사를 못 한 것이 결과에서 빠지면 그건 조용한
미탐지다.** 그래서 탐지기마다 예외를 잡아 ERROR 이벤트로 바꿔서 내보낸다.
빠지는 모듈이 없다.

## 사용법

    python main.py                          # 전부 실행, 세션 id 자동
    python main.py --session noclip_001     # 측정 실험용으로 id 지정
    python main.py --only whistle,value_tamper
    python main.py --post http://<서버>/events   # TelemetryServer 로 전송

결과는 항상 `logs/detection/<session_id>.jsonl` 에 남는다.
"""

import argparse
import datetime
import importlib
import json
import math
import os
import sys
import threading
import time
import traceback

from core.result import (DetectorResult, set_session_start, set_player_id,
                         to_team_event)

# 한글 윈도 콘솔은 기본이 cp949 라 일부 문장부호를 못 찍고 **죽는다.**
# 탐지기가 내놓는 근거 문자열에 뭐가 들어올지 모르는데, 출력하다 죽으면
# 검사 결과가 통째로 날아간다. 못 찍는 글자는 버리고 계속 찍게 한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs", "detection")

# 탐지기 등록표 — (모듈명, 임포트 경로, 한 줄 설명)
#
# 팀원이 탐지기를 추가할 때 여기 한 줄만 넣으면 된다.
# 조건은 하나: `scan()` 이 `result.DetectorResult` 를 돌려줄 것.
# 여기 있는 넷은 2번(메모리·코드 변조 감시) 몫이다. 휘파람 두 개는 핵 담당
# 쪽이라 TelemetryServer/whistle-spoofing/main.py 가 자기 등록표로 갖고 있다.
#
# **1번·3번도 이 러너를 그대로 쓸 수 있다.** 아래에 한 줄씩 넣으면 된다.
#     ("external_access", "detectors.local_guard_access", "설명"),
# 그러면 세션 id 와 기준 시각(t0)이 하나로 묶여 ReplayAnalyzer 에서 타임라인이
# 겹친다. 러너를 각자 만들면 그게 안 맞는다.
# 이 파일을 LocalGuard/main.py 로 올릴지는 은지님·동효님이 정해 주세요.
DETECTORS = [
    ("filesystem",   "detectors.filesystem",   "치트 파일 흔적 (게임 실행 불필요)"),
    ("injection",    "detectors.injection",    "주입·후킹 범용 (핵 종류 무관)"),
    ("value_tamper", "detectors.value_tamper", "값 변조 (CDO·아키타입 대조)"),
    ("overlay_hook", "detectors.overlay_hook", "인라인·렌더링 후킹 (익스포트 프롤로그)"),
]


def run_one(name, module_path):
    """탐지기 하나를 돌린다. 무슨 일이 있어도 DetectorResult 를 돌려준다.

    임포트 실패도 결과다. 모듈이 없어서 안 돈 것을 결과 목록에서 빼버리면
    "검사했는데 깨끗했다"와 구분되지 않는다.
    """
    try:
        mod = importlib.import_module(module_path)
    except Exception as e:
        return DetectorResult(name).fail(f"모듈을 불러오지 못했습니다: {e}")

    if not hasattr(mod, "scan"):
        return DetectorResult(name).fail(f"{module_path}.scan() 이 없습니다")

    try:
        res = mod.scan()
    except Exception:
        # 스택을 통째로 넣는다. 운영자가 보고 고칠 수 있어야 한다.
        return DetectorResult(name).fail(
            "검사 중 예외:\n" + traceback.format_exc(limit=6).strip())

    if not isinstance(res, DetectorResult):
        return DetectorResult(name).fail(
            f"{module_path}.scan() 이 DetectorResult 가 아닌 "
            f"{type(res).__name__} 을 돌려줬습니다")
    return res


def post(url, events):
    """TelemetryServer 로 보낸다. 실패해도 로컬 기록은 이미 끝나 있다."""
    import urllib.error
    import urllib.request

    body = json.dumps({"events": events}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, f"{resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


class Markers:
    """핵 ON/OFF 시각을 **이벤트와 같은 시계로** 기록한다.

    ReplayAnalyzer 는 탐지 지연(켠 뒤 몇 초 만에 잡혔나)과 Post-OFF(끈 뒤 몇 초
    동안 계속 잡혔나)를 잰다. 그러려면 핵을 켠 시각이 필요한데, 외부 스냅샷은
    그걸 알 방법이 없다. 그래서 2026-09-23 까지는 `cheat_start_ms` 를 비워서
    냈다 — 모르는 값을 지어내지 않으려고. 송희님 1차 분석에서 바로 그 칸이
    비어 지연시간을 못 쟀다.

    **사람이 누른 순간을 기록한다.** 핵을 켜는 손과 Enter 를 누르는 손이
    같으니 그게 가장 가까운 근사다. 시각은 `set_session_start()` 가 잡은 t0
    기준 경과 ms 라서 이벤트의 `timestamp_ms` 와 그대로 겹친다.

    ## 시작 상태를 첫 줄에 박는다

    마커는 토글이라 시작 상태를 모르면 전부 뒤집힌다. "OFF 로 시작한다"를
    코드에만 두면 데이터만 보고는 복원할 수 없다. 그래서 첫 줄에
    `{"t_ms": 0, "state": ..., "initial": true}` 를 쓴다.

    ## 줄마다 run_id 를 단다

    같은 세션 이름으로 두 번 돌리면 두 실행의 마커가 한 파일에 섞였다
    (2026-09-23 검토에서 재현). 지금은 같은 이름을 거부하지만, 어떤 경로로든
    섞였을 때 어느 실행의 줄인지 가를 수 있게 남긴다.
    """

    def __init__(self, path, t0, run_id, session_id, initial="OFF"):
        self.path = path
        self.t0 = t0
        self.run_id = run_id
        self.session_id = session_id
        self.on = initial == "ON"
        self.items = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._write({"t_ms": 0, "state": initial, "initial": True})

    def _write(self, m):
        m = dict(m, run_id=self.run_id, session_id=self.session_id)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    def toggle(self):
        with self._lock:
            self.on = not self.on
            m = {"t_ms": int((time.time() - self.t0) * 1000),
                 "state": "ON" if self.on else "OFF"}
            self.items.append(m)
            self._write(m)
        _say(f"\n  [{m['t_ms'] / 1000:7.1f}s]  핵 {m['state']} 기록")
        return m

    def listen(self):
        """Enter 를 기다린다.

        **input() 을 쓰지 않는다.** 데몬 스레드가 input() 에서 막혀 있으면
        stdout 이 파일이나 파이프일 때(`> log`, `| tee`, IDE 콘솔) 종료하면서
        stdin 버퍼 락을 못 잡아 `Fatal Python error` 로 죽었다
        (2026-09-23 검토에서 재현, 종료코드 0xC0000005).

        - 콘솔이면 msvcrt 로 키를 **폴링**한다. 막히는 호출이 없어 종료가 깨끗하고,
          시작 전에 버퍼에 남아 있던 Enter 를 먼저 비울 수 있다.
          (비우지 않으면 명령 칠 때 여분으로 누른 Enter 가 0초의 ON 으로 찍힌다.)
        - 파이프면(Git Bash/mintty 등) `os.read` 로 바이트를 읽는다. 버퍼 객체의
          락을 쥐지 않으므로 종료 때 막히지 않는다.
        """
        if sys.platform == "win32" and sys.stdin is not None and sys.stdin.isatty():
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()

            def loop():
                while not self._stop.is_set():
                    if msvcrt.kbhit():
                        if msvcrt.getwch() in ("\r", "\n"):
                            self.toggle()
                    else:
                        self._stop.wait(0.05)
        else:
            try:
                fd = sys.stdin.fileno()
            except Exception:
                _say("  ! 입력을 받을 수 없는 환경입니다. 핵 ON/OFF 표시 없이 진행합니다.")
                return False

            def loop():
                while not self._stop.is_set():
                    try:
                        b = os.read(fd, 1)
                    except OSError:
                        return
                    if not b:
                        return          # 입력이 닫혔다. 표시를 못 할 뿐 스캔은 계속
                    if b == b"\n":
                        self.toggle()

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)


def _say(msg):
    """진행 표시는 stderr 로. `--json` 을 같이 써도 stdout 은 JSON 하나만 남는다."""
    print(msg, file=sys.stderr, flush=True)


def _existing(log_dir, stem):
    return [p for p in (os.path.join(log_dir, f"{stem}{s}")
                        for s in (".jsonl", ".markers.jsonl", ".meta.json"))
            if os.path.exists(p)]


def run(session_id=None, only=None, post_url=None, log_dir=None,
        player_id=None, detectors=None, log_name=None,
        watch=0, interval=15.0, overwrite=False, start_on=False):
    detectors = DETECTORS if detectors is None else detectors
    t0 = time.time()
    set_session_start(t0)          # 모든 모듈이 같은 기준 시각을 쓰게 한다
    set_player_id(player_id)       # 로그에 누구 PC 인지 박아둔다
    run_id = f"{t0:.6f}"

    if not session_id:
        session_id = "ac_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    picked = [d for d in detectors if not only or d[0] in only]
    if only:
        unknown = set(only) - {d[0] for d in detectors}
        if unknown:
            print(f"알 수 없는 탐지기: {', '.join(sorted(unknown))}", file=sys.stderr)
            print(f"등록된 것: {', '.join(d[0] for d in detectors)}", file=sys.stderr)
            return None, 2

    # 기본은 세션별 파일이다. 구조안의 "logs/detections/각자핵.jsonl 에 계속
    # 추가" 방식은 --log-dir 과 --log-name 으로 맞출 수 있게만 열어뒀다.
    # **기본값으로 박지 않는다** — 6번 scoring 이 tail 할 규격(폴더명·파일 단위)이
    # 아직 안 정해졌고, 먼저 박으면 나중에 두 규격이 섞인다.
    log_dir = log_dir or LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    stem = log_name or session_id
    log_path = os.path.join(log_dir, f"{stem}.jsonl")

    # ── 같은 세션 이름 재사용을 막는다 ────────────────────────────────
    # 예전에는 append 라 같은 이름으로 다시 돌리면 두 실행이 한 파일에 섞였다.
    # 반복 관측에서는 더 나쁘다 — 마커까지 섞여 manifest 의 핵 구간이 두 실행을
    # 이어 붙인 값이 된다(검토에서 재현: 켠 시각은 2회차, 끈 시각은 1회차).
    # 주입을 깜빡해 Ctrl+C 하고 위 화살표 + Enter 로 다시 돌리는, 제일 흔한
    # 흐름에서 생긴다. --log-name(핵별 누적)만 예외다. 그건 누적이 목적이다.
    if not log_name:
        old = _existing(log_dir, stem)
        if old and not overwrite:
            print(f"세션 '{session_id}' 기록이 이미 있습니다:", file=sys.stderr)
            for p in old:
                print(f"    {p}", file=sys.stderr)
            print("새 --session 이름을 쓰세요. 지우고 다시 하려면 --overwrite.",
                  file=sys.stderr)
            return None, 2
        for p in old:
            os.remove(p)

    events = []

    def one_round(window_id):
        """등록된 탐지기를 한 바퀴 돌린다. **탐지기 하나 끝날 때마다 파일에 쓴다.**

        바퀴 끝에 몰아 쓰면 바퀴 도중 Ctrl+C 에 이미 끝난 탐지기 결과까지
        버려졌다(검토에서 재현). value_tamper 는 한 번에 10초가 넘는다.
        """
        out = []
        for i, (name, path, _desc) in enumerate(picked):
            started = int((time.time() - t0) * 1000)
            res = run_one(name, path)
            ended = int((time.time() - t0) * 1000)
            # 등록표의 이름을 쓴다. 탐지기가 제 이름을 다르게 적어도
            # 대시보드에서 모듈이 둘로 보이면 안 된다.
            res.detector = name
            # window_id = 몇 번째 바퀴인가, sample_id = 그 바퀴에서 몇 번째 탐지기인가.
            # 단발 실행이면 window 는 0 하나뿐이다. core/result.py 주석 참고.
            ev = to_team_event(res, session_id, window_id=window_id, sample_id=i)
            # 스캔이 **언제부터 언제까지** 메모리를 봤는가. timestamp_ms 는 끝난
            # 순간뿐이라, 10초짜리 스캔 도중에 핵을 켰으면 켜진 뒤를 봤는지 알 수
            # 없다. status 처럼 팀 스키마 밖의 확장 키로 최상위에 싣는다 —
            # evidence 안에 묻으면 분석하는 쪽이 못 찾는다.
            ev["scan_start_ms"] = started
            ev["scan_end_ms"] = ended
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            out.append(ev)
            events.append(ev)
        return out

    markers = None
    rounds = 0
    if not watch:
        one_round(0)
        rounds = 1
    else:
        initial = "ON" if start_on else "OFF"
        _write_meta(log_dir, stem, {
            "mode": "watch", "session_id": session_id, "run_id": run_id,
            "watch_s": watch, "interval_s": interval,
            "t0_epoch": t0, "initial_state": initial,
            "modules": [d[0] for d in picked],
            "rounds": 0, "ended_ms": 0}, fresh=True)
        markers = Markers(os.path.join(log_dir, f"{stem}.markers.jsonl"),
                          t0, run_id, session_id, initial)
        watching = _begin_watch(picked)
        _say(f"세션 {session_id}  —  {watch:g}초 동안 {interval:g}초마다 스캔")
        _say(f"  핵 {initial} 상태로 시작합니다. 핵을 켜거나 끌 때마다 Enter.")
        _say("  터미널은 클릭하지 말고 Alt+Tab 으로 오가세요 "
             "(클릭하면 빠른 편집 모드로 출력이 멈출 수 있습니다).")
        _say("  중간에 멈추려면 Ctrl+C. 이미 끝난 스캔은 남습니다.\n")
        markers.listen()
        try:
            while True:
                round_start = time.time()
                if round_start - t0 >= watch:
                    break
                state_before = markers.on
                got = one_round(rounds)
                _render_round(rounds, got, t0, state_before, markers.on)
                rounds += 1
                # 창을 닫아 끝내면 마지막 갱신이 안 돈다. 바퀴마다 남긴다.
                _write_meta(log_dir, stem, {
                    "rounds": rounds, "ended_ms": int((time.time() - t0) * 1000)})
                # 스캔 자체가 interval 보다 오래 걸리면 쉬지 않고 바로 다음 바퀴.
                rest = interval - (time.time() - round_start)
                if rest > 0:
                    # 남은 관측 시간을 넘겨 자지 않는다.
                    time.sleep(min(rest, max(0.0, watch - (time.time() - t0))))
        except KeyboardInterrupt:
            _say("\n  중단 — 여기까지 끝난 스캔은 저장됐습니다.")
        finally:
            markers.stop()
            _end_watch(watching)
            _write_meta(log_dir, stem, {
                "rounds": rounds, "ended_ms": int((time.time() - t0) * 1000)})

    summary = {
        "session_id": session_id,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "log": log_path,
        "events": events,
        "rounds": rounds,
    }
    if markers is not None:
        summary["markers"] = list(markers.items)
        summary["markers_log"] = markers.path
        summary["initial_state"] = "ON" if start_on else "OFF"

    if post_url:
        ok, info = post(post_url, events)
        summary["posted"] = ok
        summary["post_info"] = info

    return summary, exit_code(events)


def _begin_watch(picked):
    """반복 관측을 지원하는 탐지기에 "지금부터 새로 본다"를 알린다.

    whistle_rpc 는 후크 로그 파일을 처음부터 읽어 위반을 셌다. 반복 관측에서
    그대로 쓰면 위반이 한 번 찍힌 뒤로 **모든 바퀴가 계속 DETECTED** 가 되고,
    이전 게임 실행의 위반까지 섞인다(검토에서 재현). `begin_watch()` 가 있는
    탐지기는 지금 위치를 기준점으로 잡고, 이후 바퀴마다 새로 생긴 것만 본다.
    """
    started = []
    for name, path, _desc in picked:
        try:
            mod = importlib.import_module(path)
        except Exception:
            continue                # 불러오기 실패는 run_one 이 바퀴마다 ERROR 로 남긴다
        if hasattr(mod, "begin_watch"):
            try:
                mod.begin_watch()
                started.append(mod)
            except Exception as e:
                _say(f"  ! {name} 반복 관측 준비 실패: {e}")
    return started


def _end_watch(mods):
    for mod in mods:
        try:
            mod.end_watch()
        except Exception:
            pass


def exit_code(events):
    """0 정상 / 1 의심 이상 / 2 검사 실패가 하나라도 있음.

    **실패가 의심보다 강하다.** 검사를 못 한 세션을 '깨끗함'으로 넘기지
    않기 위해서다. 측정할 때 이 세션은 집계에서 빼야 한다.
    """
    st = {e["status"] for e in events}
    if not events or st & {"ERROR", "OFFLINE"}:
        return 2
    if st & {"DETECTED", "SUSPICIOUS", "WARNING"}:
        return 1
    return 0


def _write_meta(log_dir, stem, fields, fresh=False):
    """반복 관측의 조건(간격·회차)을 세션 옆에 남긴다.

    같은 세션 로그만 봐서는 "15초 간격으로 12바퀴 돌았다"를 복원할 수 없다.
    표본 수를 밝혀야 7번 집계가 정직해진다.

    시작할 때는 **새로 쓴다**(fresh). 이전 실행의 값과 합치면 t0 는 새 실행,
    rounds 는 옛 실행인 meta 가 남았다(검토에서 재현). 이후는 부분 갱신.
    """
    p = os.path.join(log_dir, f"{stem}.meta.json")
    cur = {}
    if not fresh and os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                cur = json.load(f)
        except Exception:
            cur = {}
    cur.update(fields)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)


def _render_round(n, events, t0, before, after):
    """반복 관측의 한 바퀴를 한 줄로. 표를 매번 찍으면 화면이 흘러가 버린다.

    핵 상태는 **바퀴 시작과 끝을 둘 다** 보여준다. 끝난 순간 상태만 찍으면,
    바퀴 도중에 켰을 때 실제로는 꺼진 상태를 스캔한 줄이 "ON" 으로 보였다.
    """
    st = lambda on: "ON " if on else "OFF"
    state = st(before) if before == after else f"{st(before).strip()}->{st(after).strip()}"
    cells = "  ".join(
        f"{e['module']}={e['status'][:4]}:{e['raw_score']}" for e in events)
    _say(f"  #{n:<3} {time.time() - t0:7.1f}s  핵 {state:<7} {cells}")


MARK = {"NORMAL": "  ", "SUSPICIOUS": "! ", "DETECTED": "!!",
        "ERROR": "X ", "OFFLINE": "- ", "WARNING": "? "}


def render(summary):
    if "markers" in summary:
        print()
        print(f"세션 {summary['session_id']}  {summary['rounds']}바퀴  "
              f"({summary['elapsed_ms'] / 1000:.1f}초)")
        print(f"로그   {summary['log']}")
        ms = summary["markers"]
        init = summary.get("initial_state", "OFF")
        print(f"마커   {summary['markers_log']}  (시작 {init}, 토글 {len(ms)}건)")
        for m in ms:
            print(f"         {m['t_ms'] / 1000:7.1f}s  {m['state']}")
        last = ms[-1]["state"] if ms else init
        if not ms and init == "OFF":
            print("         핵을 한 번도 안 켰습니다 — 정상 세션으로 내보내면 됩니다.")
        elif last == "ON":
            print("         ! 핵이 켜진 채로 끝났습니다. 끈 시각이 없어 "
                  "마지막 구간은 열린 채로 표시됩니다.")
        if summary["rounds"] == 0:
            print("         ! 한 바퀴도 못 돌았습니다. 이 세션은 쓸 수 없습니다.")
        return
    print(f"세션 {summary['session_id']}  ({summary['elapsed_ms']:,}ms)")
    print(f"로그 {summary['log']}")
    print()
    print(f"    {'모듈':<14} {'상태':<11} {'점수':>4}  근거")
    print("    " + "-" * 68)
    for ev in summary["events"]:
        reason = ", ".join(ev["reasons"]) or ev["evidence"].get("detail", "") \
            or ev["evidence"].get("error", "").splitlines()[0]
        if len(reason) > 44:
            reason = reason[:43] + "…"
        print(f" {MARK.get(ev['status'], '  ')} {ev['module']:<14} "
              f"{ev['status']:<11} {ev['raw_score']:>4}  {reason}")
    if "posted" in summary:
        print()
        print("전송 " + ("성공" if summary["posted"] else
                        f"실패 ({summary['post_info']}) — 로컬 로그는 남아 있습니다"))


def main(argv=None):
    return main_with(argv, DETECTORS)


def main_with(argv, detectors, desc="안티치트 실행기", default_log_dir=None):
    """CLI 를 등록표와 분리한다.

    핵 담당자가 자기 폴더에 러너를 두더라도 세션 묶기·공통 형식 변환·종료
    코드는 이 한 벌만 쓰게 하기 위해서다. 복사해서 쓰면 세션 id 와 기준
    시각이 두 벌이 되고, 어긋나는 순간 ReplayAnalyzer 에서 타임라인이
    안 겹친다. 그런 어긋남은 에러 없이 조용히 생긴다.
    """
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--session", help="세션 id. 측정 실험에서는 직접 지정한다")
    ap.add_argument("--only", help="쉼표로 구분한 탐지기 이름")
    ap.add_argument("--post", help="TelemetryServer 엔드포인트 URL")
    ap.add_argument("--player", help="플레이어 식별자 (기본 player_001)")
    ap.add_argument("--json", action="store_true", help="요약 대신 JSON 출력")
    ap.add_argument("--list", action="store_true", help="등록된 탐지기 목록")
    ap.add_argument("--log-dir", help="출력 폴더 (기본 logs/detection)")
    ap.add_argument("--log-name", help="출력 파일 이름 (기본 세션 id). "
                                       "핵별 누적 파일을 쓸 때 지정한다")
    ap.add_argument("--watch", type=float, default=0, metavar="SEC",
                    help="이 시간 동안 반복 스캔한다. 도중에 Enter 로 핵 ON/OFF 를 "
                         "표시하면 탐지 지연과 Post-OFF 를 잴 수 있다. "
                         "시간이 다 돼도 돌고 있던 바퀴는 끝까지 돈다")
    ap.add_argument("--interval", type=float, default=15.0, metavar="SEC",
                    help="반복 스캔 간격 (기본 15초). 스캔이 더 오래 걸리면 "
                         "쉬지 않고 바로 다음 바퀴")
    ap.add_argument("--start-on", action="store_true",
                    help="핵을 이미 켠 상태에서 시작한다 (기본은 OFF 로 시작)")
    ap.add_argument("--overwrite", action="store_true",
                    help="같은 세션 이름의 기록을 지우고 다시 쓴다")
    a = ap.parse_args(argv)

    if a.list:
        for name, path, one_line in detectors:
            print(f"  {name:<14} {one_line}   ({path}.py)")
        return 0

    only = [s.strip() for s in a.only.split(",")] if a.only else None
    # 러너마다 자기 폴더에 쓴다. 안 그러면 휘파람 세션이 2번 폴더에 섞인다.
    if a.watch:
        if not math.isfinite(a.watch) or a.watch <= 0:
            ap.error("--watch 는 0 보다 큰 초 단위 숫자여야 합니다")
        if not math.isfinite(a.interval) or a.interval < 1:
            ap.error("--interval 은 1초 이상이어야 합니다 "
                     "(0 이면 쉬지 않고 무한히 돈다)")
        if a.post:
            ap.error("--watch 와 --post 는 같이 쓸 수 없습니다 (전송은 단발 실행만)")
        if a.log_name:
            # 핵별 누적 파일에 마커까지 누적되면 어느 세션의 ON/OFF 인지 못 가른다.
            ap.error("--watch 와 --log-name 은 같이 쓸 수 없습니다 "
                     "(반복 관측은 세션별 파일로만)")
    elif a.start_on:
        ap.error("--start-on 은 --watch 와 같이 쓸 때만 의미가 있습니다")
    summary, code = run(a.session, only, a.post,
                        log_dir=a.log_dir or default_log_dir,
                        player_id=a.player, detectors=detectors,
                        log_name=a.log_name,
                        watch=a.watch, interval=a.interval,
                        overwrite=a.overwrite, start_on=a.start_on)
    if summary is None:
        return code

    if a.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        render(summary)
    return code


if __name__ == "__main__":
    sys.exit(main())
