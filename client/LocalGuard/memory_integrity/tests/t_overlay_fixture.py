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

    python client\\LocalGuard\\memory_integrity\\tests\\t_overlay_fixture.py
"""

import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
MI = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "overlay_fixture.py")
CNEW = subprocess.CREATE_NEW_PROCESS_GROUP
fails = []


def check(c, msg):
    print(("  [통과] " if c else "  [실패] ") + msg, flush=True)
    if not c:
        fails.append(msg)


def start_fixture(case, extra=()):
    p = subprocess.Popen([sys.executable, FIXTURE, "--case", case, *extra],
                         cwd=HERE, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", bufsize=1,
                         env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                         creationflags=CNEW)
    deadline = time.time() + 30
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        if line.startswith("READY "):
            return p, json.loads(line[len("READY "):])
        if line.strip():
            print("     (픽스처) " + line.strip()[:160])
    p.kill()
    raise SystemExit(f"픽스처가 준비되지 않았습니다: {case}")


def stop_fixture(p):
    if p and p.poll() is None:
        try:
            p.stdin.write("q\n")
            p.stdin.flush()
            p.wait(timeout=10)
        except Exception:
            p.kill()


def scan(pid, session):
    """탐지기를 그 PID 에 붙여 공통 이벤트를 받는다."""
    r = subprocess.run([sys.executable, os.path.join("detectors", "overlay_hook.py"), session],
                       cwd=MI, capture_output=True, text=True, encoding="utf-8",
                       errors="replace",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8",
                                GZZ_OVERLAY_VALIDATION_PID=str(pid)))
    try:
        return json.loads(r.stdout[r.stdout.index("{"):]), r.returncode
    except Exception:
        raise SystemExit(f"탐지기 출력을 못 읽었습니다 (code {r.returncode}):\n"
                         f"{r.stdout[-800:]}\n{r.stderr[-800:]}")


def show(ev, want_score):
    meta = ev["evidence"].get("meta", {})
    print(f"     raw_score {ev['raw_score']} / status {ev['status']} / "
          f"scope {meta.get('measurement_scope')} / pid {meta.get('target_pid')}")
    for rs in ev["reasons"]:
        print(f"       - {rs}")
    return ev["raw_score"] == want_score


CASES = [
    ("normal", 0, "NORMAL", None, None),
    ("general-unsigned", 60, "DETECTED", "ws2_32.dll", "서명없음"),
    ("general-unverifiable", 60, "DETECTED", "ws2_32.dll", "확인불가"),
    ("render-unsigned", 100, "DETECTED", "dxgi.dll", "서명없음"),
    ("render-unverifiable", 100, "DETECTED", "dxgi.dll", "확인불가"),
]

print("1) 경우별 점수·evidence")
for case, want, want_status, want_dll, want_sig in CASES:
    print(f"  {case}  (기대 {want}점)")
    fx = None
    try:
        fx, info = start_fixture(case)
        ev, code = scan(info["pid"], f"harness_{case.replace('-', '_')}")
        check(show(ev, want), f"{case}: raw_score {ev['raw_score']} (기대 {want})")
        check(ev["status"] == want_status, f"{case}: status {ev['status']} (기대 {want_status})")
        meta = ev["evidence"].get("meta", {})
        check(meta.get("measurement_scope") == "controlled_harness",
              f"{case}: evidence 에 controlled_harness 라벨")
        check(meta.get("target_pid") == info["pid"], f"{case}: 하네스 PID 를 봤다")
        if want_dll:
            blob = json.dumps(ev, ensure_ascii=False)
            check(info["target_export"] in blob,
                  f"{case}: evidence 에 익스포트 이름 {info['target_export']}")
            check(want_sig in blob, f"{case}: evidence 에 서명 판정 '{want_sig}'")
            if want == 100:
                check("overlay_hook" in ev["reasons"] or "렌더링" in blob,
                      f"{case}: 렌더링 경로 사유가 따로 붙음")
    finally:
        stop_fixture(fx)

print("2) 패턴 제거 후 0점 복귀 (같은 프로세스)")
fx = None
try:
    fx, info = start_fixture("render-unsigned")
    ev, _ = scan(info["pid"], "harness_before_remove")
    check(ev["raw_score"] == 100, f"제거 전 100점 ({ev['raw_score']})")
    fx.stdin.write("remove\n")
    fx.stdin.flush()
    line, deadline = "", time.time() + 15
    while time.time() < deadline:
        line = fx.stdout.readline()
        if line.startswith("REMOVED "):
            break
    print("     " + line.strip()[:120])
    ev2, _ = scan(info["pid"], "harness_after_remove")
    show(ev2, 0)
    check(line.startswith("REMOVED ") and ev2["raw_score"] == 0 and ev2["status"] == "NORMAL",
          f"제거 후 0점 NORMAL 복귀 ({ev2['raw_score']}, {ev2['status']})")
    check(ev2["evidence"].get("meta", {}).get("target_pid") == info["pid"],
          "같은 프로세스에서 다시 쟀다 (재시작 아님)")
finally:
    stop_fixture(fx)

print("3) 프로세스 재시작 후 0점")
fx = None
try:
    fx, info = start_fixture("normal")
    ev, code = scan(info["pid"], "harness_restart_normal")
    show(ev, 0)
    check(ev["raw_score"] == 0 and ev["status"] == "NORMAL" and code == 0,
          f"새로 띄운 하네스는 0점 NORMAL, 종료코드 0 ({ev['raw_score']}, {ev['status']}, {code})")
    meta = ev["evidence"].get("meta", {})
    check(meta.get("exports_checked", 0) > 0 and meta.get("modules_scanned"),
          f"검사는 실제로 했다 (익스포트 {meta.get('exports_checked')}개, "
          f"모듈 {len(meta.get('modules_scanned') or [])}개)")
finally:
    stop_fixture(fx)

print("4) 게임 경로는 그대로 (하네스 지정 없으면 게임을 본다)")
r = subprocess.run([sys.executable, os.path.join("detectors", "overlay_hook.py"), "harness_gamepath"],
                   cwd=MI, capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env={k: v for k, v in dict(os.environ, PYTHONIOENCODING="utf-8").items()
                        if k != "GZZ_OVERLAY_VALIDATION_PID"})
ev = json.loads(r.stdout[r.stdout.index("{"):]) if "{" in r.stdout else {}
print(f"     status {ev.get('status')} / {str(ev.get('evidence', {}).get('error'))[:60]}")
check(ev.get("evidence", {}).get("meta", {}).get("measurement_scope") != "controlled_harness",
      "지정이 없으면 harness 라벨이 안 붙는다")
bad = subprocess.run([sys.executable, os.path.join("detectors", "overlay_hook.py"), "harness_bad"],
                     cwd=MI, capture_output=True, text=True, encoding="utf-8", errors="replace",
                     env=dict(os.environ, PYTHONIOENCODING="utf-8",
                              GZZ_OVERLAY_VALIDATION_PID="nope"))
check("nope" in (bad.stdout + bad.stderr) or bad.returncode != 0,
      "PID 가 숫자가 아니면 조용히 게임으로 넘어가지 않고 알린다")

print("\n" + ("전부 통과" if not fails else f"실패 {len(fails)}건: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
