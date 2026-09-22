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
import os
import sys
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


def run(session_id=None, only=None, post_url=None, log_dir=None,
        player_id=None, detectors=None, log_name=None):
    detectors = DETECTORS if detectors is None else detectors
    t0 = time.time()
    set_session_start(t0)          # 모든 모듈이 같은 기준 시각을 쓰게 한다
    set_player_id(player_id)       # 로그에 누구 PC 인지 박아둔다

    if not session_id:
        session_id = "ac_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    picked = [d for d in detectors if not only or d[0] in only]
    if only:
        unknown = set(only) - {d[0] for d in detectors}
        if unknown:
            print(f"알 수 없는 탐지기: {', '.join(sorted(unknown))}", file=sys.stderr)
            print(f"등록된 것: {', '.join(d[0] for d in detectors)}", file=sys.stderr)
            return None, 2

    events = []
    for i, (name, path, _desc) in enumerate(picked):
        res = run_one(name, path)
        # 등록표의 이름을 쓴다. 탐지기가 제 이름을 다르게 적어도
        # 대시보드에서 모듈이 둘로 보이면 안 된다.
        res.detector = name
        # sample_id 는 이 세션에서 몇 번째 이벤트인가다. 한 번 스캔이라
        # window 는 하나뿐이고(0), 순서만 남긴다. core/result.py 주석 참고.
        events.append(to_team_event(res, session_id, sample_id=i))

    # 기본은 세션별 파일이다. 구조안의 "logs/detections/각자핵.jsonl 에 계속
    # 추가" 방식은 --log-dir 과 --log-name 으로 맞출 수 있게만 열어뒀다.
    # **기본값으로 박지 않는다** — 6번 scoring 이 tail 할 규격(폴더명·파일 단위)이
    # 아직 안 정해졌고, 먼저 박으면 나중에 두 규격이 섞인다.
    log_dir = log_dir or LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{log_name or session_id}.jsonl")
    with open(log_path, "a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    summary = {
        "session_id": session_id,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "log": log_path,
        "events": events,
    }

    if post_url:
        ok, info = post(post_url, events)
        summary["posted"] = ok
        summary["post_info"] = info

    return summary, exit_code(events)


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


MARK = {"NORMAL": "  ", "SUSPICIOUS": "! ", "DETECTED": "!!",
        "ERROR": "X ", "OFFLINE": "- ", "WARNING": "? "}


def render(summary):
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
    a = ap.parse_args(argv)

    if a.list:
        for name, path, one_line in detectors:
            print(f"  {name:<14} {one_line}   ({path}.py)")
        return 0

    only = [s.strip() for s in a.only.split(",")] if a.only else None
    # 러너마다 자기 폴더에 쓴다. 안 그러면 휘파람 세션이 2번 폴더에 섞인다.
    summary, code = run(a.session, only, a.post,
                        log_dir=a.log_dir or default_log_dir,
                        player_id=a.player, detectors=detectors,
                        log_name=a.log_name)
    if summary is None:
        return code

    if a.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        render(summary)
    return code


if __name__ == "__main__":
    sys.exit(main())
