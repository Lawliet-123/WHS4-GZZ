"""MECCHA 안티치트 런처.

사용자가 LocalGuard·SelfDefense·KernelWatcher·탐지기·게임을 각각 찾아 실행하지
않도록, 이것 하나가 순서대로 켜고 상태를 보여주고 끝날 때 정리한다.
나중에 MecchaAntiCheat.exe 로 패키징할 대상이다.

    python client/Launcher/main.py
    python client/Launcher/main.py --session test_001 --player player_002
    python client/Launcher/main.py --no-launch-game     # 게임은 내가 직접 켠다
    python client/Launcher/main.py --only memory_integrity,whistle_spoofing

## 켜는 순서

    1. 게임과 무관한 것 먼저   SelfDefense, KernelWatcher
       게임이 뜨는 순간부터 보고 있어야 하므로 게임보다 앞선다.
    2. 게임
    3. 게임이 실제로 뜰 때까지 대기
    4. 게임이 있어야 의미가 있는 것   LocalGuard 3종, 핵별 탐지기
       게임이 없으면 이 모듈들은 OFFLINE 만 찍는다. 그건 "깨끗함"이 아니라
       "검사를 못 한 것"이라, 굳이 그 상태로 로그를 쌓지 않는다.
    5. 게임이 꺼지면 전부 정리

## 안 만들어진 모듈이 있어도 멈추지 않는다

지금 SelfDefense·KernelWatcher·input_signature 는 폴더만 있다. 없는 모듈에서
런처가 죽으면 다른 사람이 자기 것을 시험해볼 수 없다. **없으면 MISSING 으로
보여주고 나머지를 계속 띄운다.** 조용히 넘기지도 않는다 — 아직 안 만든 것과
만들었는데 안 붙는 것은 원인이 다르다.
"""

import argparse
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import game_launcher                                          # noqa: E402
import ui                                                     # noqa: E402
from modules import MODULES, REPO                              # noqa: E402
from process_manager import MISSING, ProcessManager, SKIPPED, is_admin  # noqa: E402


def existing_sessions(picked, session):
    """이 세션 이름으로 이미 쌓인 로그가 있는지 본다.

    주기 검사 모듈은 한 세션 안에서 여러 번 실행되므로 **누적 파일**에 쓴다
    (`--log-name`). 그래서 run_session.py 의 "같은 이름 거부" 가 걸리지 않는다.
    그대로 두면 런처를 같은 이름으로 두 번 돌렸을 때 두 실행이 한 파일에 섞이고,
    시각이 되돌아가 replay_export 가 **측정이 다 끝난 뒤에** 거부한다.
    실제로 첫 실전(run_001)에서 그렇게 됐다. 그래서 시작 전에 막는다.
    """
    found = []
    for m in picked:
        if not m.session_log_dir:
            continue
        p = os.path.join(REPO, m.session_log_dir, f"{session}.jsonl")
        if os.path.exists(p):
            found.append(os.path.relpath(p, REPO))
    return found


def preflight(pm, only):
    ui.line("=" * 76)
    ui.line("  MECCHA 안티치트 런처")
    ui.line("=" * 76)
    if not is_admin():
        ui.line("  ! 관리자 권한이 아닙니다. 커널 모듈은 건너뜁니다.")
    missing = [s for s in pm.states.values() if s.status == MISSING]
    if missing:
        ui.line("")
        ui.line("  아직 안 올라온 모듈 (런처는 그대로 진행합니다):")
        for s in missing:
            ui.line(f"    - {s.name:<18} {s.detail}")
    if only:
        ui.line(f"  --only: {', '.join(only)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="MECCHA 안티치트 런처")
    ap.add_argument("--session", help="세션 id (기본: 시각으로 자동 생성)")
    ap.add_argument("--player", default="player_001")
    ap.add_argument("--only", help="쉼표로 구분한 모듈 이름만 실행")
    ap.add_argument("--no-launch-game", action="store_true",
                    help="게임을 띄우지 않고, 이미 떠 있는 게임을 기다린다")
    ap.add_argument("--wait-game", type=float, default=180.0, metavar="SEC",
                    help="게임이 뜨기를 기다리는 시간 (기본 180초)")
    ap.add_argument("--status-every", type=float, default=10.0, metavar="SEC",
                    help="상태 화면을 몇 초마다 그릴지 (기본 10초)")
    ap.add_argument("--overwrite", action="store_true",
                    help="같은 세션 이름의 기존 로그를 지우고 다시 쓴다")
    a = ap.parse_args(argv)

    session = a.session or ("ac_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    only = [s.strip() for s in a.only.split(",")] if a.only else None
    picked = [m for m in MODULES if not only or m.name in only]
    if only:
        unknown = set(only) - {m.name for m in MODULES}
        if unknown:
            ui.line(f"알 수 없는 모듈: {', '.join(sorted(unknown))}")
            ui.line(f"등록된 것: {', '.join(m.name for m in MODULES)}")
            return 2

    old = existing_sessions(picked, session)
    if old:
        if not a.overwrite:
            ui.line(f"세션 '{session}' 기록이 이미 있습니다:")
            for p in old:
                ui.line(f"    {p}")
            ui.line("새 --session 이름을 쓰세요. 지우고 다시 하려면 --overwrite.")
            return 2
        for p in old:
            os.remove(os.path.join(REPO, p))
            ui.line(f"  지움: {p}")

    # 세션 전체가 같은 시계를 쓴다. 주기 검사는 실행마다 새 프로세스라
    # 이걸 안 넘기면 시각이 매번 0 으로 되돌아가고 타임라인이 깨진다.
    t0 = time.time()
    pm = ProcessManager(picked, session, a.player, t0, say=ui.line)
    preflight(pm, only)

    ctx = {"session": session, "game_pid": None, "server": "미연결"}
    code = 0
    try:
        ui.line("")
        ui.line("  [1/4] 게임과 무관한 모듈 시작")
        pm.start_group(needs_game=False)

        pid = game_launcher.find_game_pid()
        if pid is None and not a.no_launch_game:
            ui.line("  [2/4] 게임 실행")
            if not game_launcher.launch():
                ui.line("      게임을 띄우지 못했습니다. 직접 켜 주세요.")
        elif pid is not None:
            ui.line(f"  [2/4] 게임이 이미 떠 있습니다 (PID {pid})")

        if pid is None:
            ui.line(f"  [3/4] 게임을 기다리는 중 (최대 {a.wait_game:.0f}초)")
            pid = game_launcher.wait_for_game(a.wait_game)
        if pid is None:
            ui.line("  게임이 뜨지 않아 종료합니다. 게임을 켜고 다시 실행해 주세요.")
            return 2
        ctx["game_pid"] = pid

        ui.line("  [4/4] 게임 관련 모듈 시작")
        pm.start_group(needs_game=True)

        ui.line("")
        ui.line("  Ctrl+C 로 종료합니다. 게임이 꺼져도 자동으로 정리합니다.")
        last_draw = 0.0
        while True:
            pm.poll()
            if game_launcher.find_game_pid() is None:
                ui.line("")
                ui.line("  게임이 종료되었습니다. 모듈을 정리합니다.")
                break
            now = time.time()
            if now - last_draw >= a.status_every:
                ui.render(pm.snapshot(), ctx)
                last_draw = now
            time.sleep(0.5)
    except KeyboardInterrupt:
        ui.line("")
        ui.line("  중단합니다.")
    finally:
        pm.stop_all()
        ui.render(pm.snapshot(), ctx)
        ui.line("")
        ui.line(f"  세션 {session} 종료. 모듈 로그: client/Launcher/logs/")

    # 런처 자체의 성공/실패만 돌려준다. 탐지 결과는 각 모듈 로그에 있다.
    failed = [s for s in pm.states.values()
              if s.status == "FAILED"]
    if failed:
        ui.line(f"  ! 비정상 종료한 모듈 {len(failed)}개: "
                + ", ".join(s.name for s in failed))
        code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
