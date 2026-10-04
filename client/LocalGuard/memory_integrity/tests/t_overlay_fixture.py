"""overlay_hook 판정 경로 — 하네스(controlled_harness)로 6가지 경우 실측.

은지님 10/5 요청 그대로:
    정상 진입부                       0점
    일반 익스포트 + 서명 없음         60
    일반 익스포트 + 서명 확인 불가    60
    렌더링 익스포트 + 서명 없음       100
    렌더링 익스포트 + 서명 확인 불가  100
    패턴 제거 후 / 프로세스 재시작 후 0점 복귀

각 경우의 raw_score·status·reasons 와 evidence(대상 DLL·익스포트, 목적지, 서명 판정)가
보존되는지 같이 본다. 게임은 켜지 않는다 — 하네스는 자기 메모리만 바꾼다.

탐지기는 **함수 인자로만** 하네스를 본다(`scan(target_pid=...)`). 환경변수 경로는
없앴다 — 런처가 환경을 자식에게 물려줘서 운영 중 탐지 대상을 바꾸는 우회가 됐다
(10/5 은지님 지적).

    python tests\\t_overlay_fixture.py
    python tests\\t_overlay_fixture.py --unsigned-dll <경로>
        서명 없는 모듈이 자동으로 안 잡히는 PC 에서 unsigned 두 경우에 쓸 DLL
    python tests\\t_overlay_fixture.py --central-url http://127.0.0.1:8002
        중앙 전송(Shared -> Receiver -> Scoring)까지. 서버가 떠 있을 때만
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
MI = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "overlay_fixture.py")
sys.path.insert(0, MI)
from detectors import overlay_hook as OH                       # noqa: E402
from core.result import to_team_event, set_session_start, set_player_id  # noqa: E402

CNEW = subprocess.CREATE_NEW_PROCESS_GROUP
fails = []
skipped = []


def check(c, msg):
    print(("  [통과] " if c else "  [실패] ") + msg, flush=True)
    if not c:
        fails.append(msg)


def skip(msg):
    print("  [건너뜀] " + msg, flush=True)
    skipped.append(msg)


def start_fixture(case, unsigned_dll=None):
    """하네스를 띄우고 READY 줄을 읽는다. (프로세스, 정보) 또는 (None, 사유)."""
    argv = [sys.executable, FIXTURE, "--case", case]
    if unsigned_dll:
        argv += ["--unsigned-dll", unsigned_dll]
    p = subprocess.Popen(argv, cwd=HERE, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", bufsize=1,
                         env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                         creationflags=CNEW)
    out, deadline = [], time.time() + 30
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        if line.startswith("READY "):
            return p, json.loads(line[len("READY "):])
        if line.strip():
            out.append(line.strip())
    p.kill()
    return None, " / ".join(out[-3:]) or "READY 줄이 안 나왔습니다"


def stop_fixture(p):
    if p and p.poll() is None:
        try:
            p.stdin.write("q\n")
            p.stdin.flush()
            p.wait(timeout=10)
        except Exception:
            p.kill()


def scan_event(pid, session, player="pc_harness"):
    """탐지기를 그 PID 에 붙여 공통 이벤트를 만든다. run_session 과 같은 변환."""
    set_session_start(time.time())
    set_player_id(player)
    res = OH.scan(target_pid=pid)
    return to_team_event(res, session, player_id=player)


def show(ev):
    meta = ev["evidence"].get("meta", {})
    print(f"     raw_score {ev['raw_score']} / status {ev['status']} / "
          f"scope {meta.get('measurement_scope')} / pid {meta.get('target_pid')}")
    for rs in ev["reasons"]:
        print(f"       - {rs}")


CASES = [
    ("normal", 0, "NORMAL", None, None),
    ("general-unsigned", 60, "DETECTED", "ws2_32.dll", "서명없음"),
    ("general-unverifiable", 60, "DETECTED", "ws2_32.dll", "확인불가"),
    ("render-unsigned", 100, "DETECTED", "dxgi.dll", "서명없음"),
    ("render-unverifiable", 100, "DETECTED", "dxgi.dll", "확인불가"),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description="overlay_hook 하네스 실측")
    ap.add_argument("--unsigned-dll", default=None,
                    help="서명 없는 모듈이 자동으로 안 잡힐 때 unsigned 경우에 쓸 DLL")
    ap.add_argument("--central-url", default=None,
                    help="중앙 전송까지 확인할 서버 주소 (예: http://127.0.0.1:8002)")
    ap.add_argument("--central-token", default="local-e2e-det")
    a = ap.parse_args(argv)

    print("1) 경우별 점수·evidence")
    for case, want, want_status, want_dll, want_sig in CASES:
        print(f"  {case}  (기대 {want}점)")
        fx = None
        try:
            fx, info = start_fixture(case, a.unsigned_dll)
            if fx is None:
                # 서명 없는 모듈이 없는 PC 는 unsigned 두 경우를 못 쟨다.
                # 조용히 넘기지 않고 왜 못 쟀는지 남긴다(--unsigned-dll 로 풀 수 있다).
                if "unsigned" in case and "서명 없는 모듈이 없습니다" in info:
                    skip(f"{case}: 이 PC 에 서명 없는 모듈이 없음 — "
                         f"--unsigned-dll <경로> 로 하나 주면 잴 수 있습니다")
                    continue
                check(False, f"{case}: 하네스가 안 떴습니다 — {info[:120]}")
                continue
            ev = scan_event(info["pid"], f"harness_{case.replace('-', '_')}")
            show(ev)
            check(ev["raw_score"] == want, f"{case}: raw_score {ev['raw_score']} (기대 {want})")
            check(ev["status"] == want_status,
                  f"{case}: status {ev['status']} (기대 {want_status})")
            meta = ev["evidence"].get("meta", {})
            check(meta.get("measurement_scope") == "controlled_harness",
                  f"{case}: evidence 에 controlled_harness 라벨")
            check(meta.get("target_pid") == info["pid"], f"{case}: 하네스 PID 를 봤다")
            if want_dll:
                blob = json.dumps(ev, ensure_ascii=False)
                check(info["target_export"] in blob,
                      f"{case}: evidence 에 익스포트 이름 {info['target_export']}")
                check(want_sig in blob, f"{case}: evidence 에 서명 판정 '{want_sig}'")
                # 중앙 정책이 자유 문장을 파싱하지 않고 가를 수 있어야 한다(송희님 10/5)
                uh = (ev["evidence"].get("meta", {}) or {}).get("untrusted_hookers") or []
                check(len(uh) == 1 and uh[0].get("signature") == want_sig
                      and uh[0].get("render") == (want_dll == "dxgi.dll")
                      and uh[0].get("hooks") == 1,
                      f"{case}: meta.untrusted_hookers 로 서명 상태를 구조화해서 넘김 "
                      f"({uh[0] if uh else '없음'})")
                if want == 100:
                    check("overlay_hook" in ev["reasons"] or "렌더링" in blob,
                          f"{case}: 렌더링 경로 사유가 따로 붙음")
        finally:
            stop_fixture(fx)

    print("2) 패턴 제거 후 0점 복귀 (같은 프로세스)")
    fx = None
    try:
        fx, info = start_fixture("render-unverifiable")   # 서명 없는 모듈이 없어도 된다
        if fx is None:
            check(False, f"하네스가 안 떴습니다 — {str(info)[:120]}")
        else:
            ev = scan_event(info["pid"], "harness_before_remove")
            check(ev["raw_score"] == 100, f"제거 전 100점 ({ev['raw_score']})")
            fx.stdin.write("remove\n")
            fx.stdin.flush()
            line, deadline = "", time.time() + 15
            while time.time() < deadline:
                line = fx.stdout.readline()
                if line.startswith("REMOVED "):
                    break
            print("     " + line.strip()[:120])
            ev2 = scan_event(info["pid"], "harness_after_remove")
            show(ev2)
            check(line.startswith("REMOVED ") and ev2["raw_score"] == 0
                  and ev2["status"] == "NORMAL",
                  f"제거 후 0점 NORMAL 복귀 ({ev2['raw_score']}, {ev2['status']})")
            check(ev2["evidence"].get("meta", {}).get("target_pid") == info["pid"],
                  "같은 프로세스에서 다시 쟀다 (재시작 아님)")
    finally:
        stop_fixture(fx)

    print("3) 프로세스 재시작 후 0점")
    fx = None
    try:
        fx, info = start_fixture("normal")
        ev = scan_event(info["pid"], "harness_restart_normal")
        show(ev)
        check(ev["raw_score"] == 0 and ev["status"] == "NORMAL",
              f"새로 띄운 하네스는 0점 NORMAL ({ev['raw_score']}, {ev['status']})")
        meta = ev["evidence"].get("meta", {})
        check(meta.get("exports_checked", 0) > 0 and meta.get("modules_scanned"),
              f"검사는 실제로 했다 (익스포트 {meta.get('exports_checked')}개, "
              f"모듈 {len(meta.get('modules_scanned') or [])}개)")
    finally:
        stop_fixture(fx)

    print("4) 운영 경로는 게임만 본다")
    ev = scan_event(None, "harness_gamepath") if False else to_team_event(
        OH.scan(), "harness_gamepath", player_id="pc_harness")
    print(f"     status {ev['status']} / {str(ev['evidence'].get('error'))[:60]}")
    check(ev["evidence"].get("meta", {}).get("measurement_scope") != "controlled_harness",
          "인자를 안 주면 harness 라벨이 안 붙는다 (게임을 찾는다)")
    os.environ["GZZ_OVERLAY_VALIDATION_PID"] = "99999"
    try:
        ev2 = to_team_event(OH.scan(), "harness_envcheck", player_id="pc_harness")
        check(ev2["evidence"].get("meta", {}).get("measurement_scope") != "controlled_harness",
              "환경변수로는 대상을 못 바꾼다 (운영 중 탐지 회피 경로 없음)")
    finally:
        os.environ.pop("GZZ_OVERLAY_VALIDATION_PID", None)

    if a.central_url:
        print("5) 중앙 전송 (Shared -> Receiver -> Scoring)")
        fx = None
        try:
            fx, info = start_fixture("render-unverifiable")
            session = f"harness_central_{int(time.time())}"
            import tempfile
            work = tempfile.mkdtemp(prefix="harness_central_")
            os.environ.update(GZZ_TELEMETRY_URL=a.central_url,
                              GZZ_TELEMETRY_TOKEN=a.central_token,
                              GZZ_TELEMETRY_ALLOW_INSECURE_LOOPBACK="true",
                              GZZ_TELEMETRY_OUTBOX=os.path.join(work, "client.sqlite3"))
            from core import telemetry
            from core.result import to_shared_event
            ev = scan_event(info["pid"], session)
            tele = telemetry.make(os.path.join(work, "outbox"))
            tele.send(to_shared_event(ev))
            state = tele.finish()
            print(f"     {session}: {ev['raw_score']}점 전송 -> {state}")
            check(ev["raw_score"] == 100 and str(state).find("error") < 0,
                  f"100점 Event 가 중앙으로 나감 ({state})")
            import shutil
            shutil.rmtree(work, ignore_errors=True)
        finally:
            stop_fixture(fx)
            for k in ("GZZ_TELEMETRY_URL", "GZZ_TELEMETRY_TOKEN",
                      "GZZ_TELEMETRY_ALLOW_INSECURE_LOOPBACK", "GZZ_TELEMETRY_OUTBOX"):
                os.environ.pop(k, None)

    tail = (f"전부 통과 (건너뜀 {len(skipped)}건)" if skipped else "전부 통과") if not fails \
        else f"실패 {len(fails)}건: " + "; ".join(fails)
    print("\n" + tail)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
