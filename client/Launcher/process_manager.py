"""모듈 프로세스의 실행·생존 확인·종료.

## 워치독(4번)과 역할이 겹치지 않게 선을 그었다

둘 다 "모듈이 죽었는지" 를 보지만 하는 일이 다르다.

    런처(여기)   실행하고, 상태를 보고, 끝낼 때 정리한다. **되살리지 않는다.**
    워치독(4번)  죽었는지 감지해서 보고하고, 필요하면 되살린다.

둘 다 재시작하면 같은 모듈을 두 번 띄우거나 서로 죽인 것을 되살리려고 싸운다.
그래서 이 파일에는 재시작 코드가 없다. 주기 실행(ONESHOT)은 "죽어서 되살리는 것"이
아니라 "원래 주기적으로 도는 검사"라서 다르다.

## 출력은 모듈마다 파일로 뺀다

모듈 7개가 한 콘솔에 같이 찍으면 읽을 수 없고, 무엇보다 파이프가 가득 차면
자식 프로세스가 멈춘다(Windows 파이프 버퍼는 몇 KB뿐이다). 그래서 각자
`client/Launcher/logs/<모듈>.log` 로 보낸다. 문제가 생기면 그 파일을 보면 된다.
"""

import ctypes
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from modules import CONTINUOUS, ONESHOT, REPO, Module

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# 상태값. ui.py 가 이걸 보고 화면을 그린다.
MISSING = "MISSING"    # 파일이 없다 = 아직 구현 전
SKIPPED = "SKIPPED"    # 조건이 안 맞아 건너뜀 (관리자 권한 등)
PENDING = "PENDING"    # 등록됐고 아직 시작 전 (게임을 기다리는 중)
RUNNING = "RUNNING"
DONE = "DONE"          # 한 번 돌고 정상 종료
WARN = "WARN"          # 돌긴 했는데 검사가 성립하지 않음 (종료코드 2)
FAILED = "FAILED"      # 비정상 종료
STOPPED = "STOPPED"    # 우리가 끝냈다


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


@dataclass
class ModuleState:
    module: Module
    status: str = PENDING
    proc: Optional[subprocess.Popen] = None
    started_at: float = 0.0
    last_code: Optional[int] = None
    runs: int = 0
    next_run_at: float = 0.0
    detail: str = ""
    log_path: str = ""

    @property
    def name(self) -> str:
        return self.module.name

    def snapshot(self) -> dict:
        """ui.py 로 넘기는 한 줄 요약. **여기가 동효님과의 접점이다.**"""
        return {
            "name": self.name,
            "owner": self.module.owner,
            "status": self.status,
            "mode": self.module.mode,
            "runs": self.runs,
            "last_code": self.last_code,
            "uptime_s": (time.time() - self.started_at) if self.status == RUNNING else 0.0,
            "detail": self.detail,
            "log": self.log_path,
        }


class ProcessManager:
    def __init__(self, modules: List[Module], session: str, player: str,
                 t0: float, say=print):
        self.session = session
        self.player = player
        self.t0 = t0
        self.say = say
        self.states: Dict[str, ModuleState] = {}
        os.makedirs(LOG_DIR, exist_ok=True)
        admin = is_admin()
        for m in modules:
            st = ModuleState(m)
            st.log_path = os.path.join(LOG_DIR, f"{m.name}.log")
            path = m.script_path()
            if path and not os.path.exists(path):
                # **없는 모듈을 조용히 넘기지 않는다.** 아직 안 만든 것과
                # 만들었는데 안 붙는 것은 원인이 완전히 다르다.
                st.status = MISSING
                st.detail = f"{os.path.relpath(path, REPO)} 없음 — {m.owner}"
            elif m.needs_admin and not admin:
                st.status = SKIPPED
                st.detail = "관리자 권한으로 실행해야 합니다"
            self.states[m.name] = st

    # ── 실행 ───────────────────────────────────────────────────────────
    def start(self, name: str) -> bool:
        st = self.states[name]
        if st.status in (MISSING, SKIPPED):
            return False
        if st.proc is not None and st.proc.poll() is None:
            return True                      # 이미 돌고 있다

        argv = st.module.resolved({
            "session": self.session, "player": self.player,
            "t0": f"{self.t0:.3f}", "window": st.runs,
        })
        cwd = st.module.cwd or REPO
        header = (f"\n{'=' * 70}\n"
                  f"[launcher] {time.strftime('%H:%M:%S')}  run #{st.runs + 1}\n"
                  f"[launcher] {' '.join(argv)}\n{'=' * 70}\n")
        try:
            log = open(st.log_path, "a", encoding="utf-8")
            log.write(header)
            log.flush()
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            st.proc = subprocess.Popen(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, env=env)
        except Exception as e:
            st.status = FAILED
            st.detail = f"실행 실패: {e}"
            self.say(f"  ! {name} 실행 실패 — {e}")
            return False

        st.status = RUNNING
        st.started_at = time.time()
        st.runs += 1
        st.detail = ""
        return True

    def start_group(self, needs_game: bool) -> None:
        for st in self.states.values():
            if st.module.needs_game == needs_game and st.status == PENDING:
                self.start(st.name)

    # ── 감시 ───────────────────────────────────────────────────────────
    def poll(self) -> None:
        """상태를 갱신하고, 주기 실행이 올 차례면 다시 부른다. 되살리지는 않는다."""
        now = time.time()
        for st in self.states.values():
            if st.proc is not None and st.status == RUNNING:
                code = st.proc.poll()
                if code is not None:
                    st.proc = None
                    st.last_code = code
                    if st.module.mode == ONESHOT:
                        # run_session.py 의 계약: 0 정상 / 1 의심 / 2 검사 실패
                        st.status = {0: DONE, 1: DONE, 2: WARN}.get(code, FAILED)
                        st.detail = {0: "정상", 1: "의심 발견", 2: "검사 실패"}.get(
                            code, f"비정상 종료 (code {code})")
                        st.next_run_at = now + st.module.every_s if st.module.every_s else 0.0
                    else:
                        # 계속 돌아야 하는 모듈이 끝났다. 정상이 아니다.
                        st.status = FAILED
                        st.detail = f"상주 모듈이 종료됨 (code {code}) — 로그 확인"
                        self.say(f"  ! {st.name} 이 멈췄습니다. {st.log_path}")

            if (st.status in (DONE, WARN) and st.module.every_s
                    and st.next_run_at and now >= st.next_run_at):
                self.start(st.name)

    def snapshot(self) -> List[dict]:
        return [self.states[n].snapshot() for n in self.states]

    def running_count(self) -> int:
        return sum(1 for s in self.states.values() if s.status == RUNNING)

    # ── 종료 ───────────────────────────────────────────────────────────
    def stop_all(self, grace_s: float = 5.0) -> None:
        """전부 끝낸다. 먼저 정중히, 안 되면 강제로.

        주기 검사 도중에 끊으면 그때까지 끝난 탐지기 결과는 이미 파일에 있다
        (run_session 이 탐지기 하나 끝날 때마다 쓴다). 그래서 중간에 끊어도
        관측이 통째로 날아가지 않는다.
        """
        alive = [s for s in self.states.values()
                 if s.proc is not None and s.proc.poll() is None]
        for st in alive:
            try:
                st.proc.terminate()
            except Exception:
                pass
        deadline = time.time() + grace_s
        for st in alive:
            left = max(0.0, deadline - time.time())
            try:
                st.proc.wait(timeout=left)
            except Exception:
                try:
                    st.proc.kill()
                    self.say(f"  - {st.name} 강제 종료")
                except Exception:
                    pass
            st.proc = None
            if st.status == RUNNING:
                st.status = STOPPED
