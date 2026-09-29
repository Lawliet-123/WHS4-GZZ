"""모듈 프로세스의 실행·생존 확인·재시작·종료.

## 재시작 — 런처와 워치독(4번)이 **둘 다** 한다

2026-09-29 성민님 제안으로, 죽은 상주 모듈은 런처도 되살리고 워치독도 되살린다.
둘이 따로 되살려도 충돌하지 않도록 규칙은 전부 registry.py 한 곳에 있다.

    - 모듈마다 잠금을 잡은 쪽만 띄운다. 상대가 이미 되살렸으면 이어받는다
    - 누가 띄우든 등록부(logs/anticheat_pids.json)에 적는다
    - 재시작 한도(5분에 5번)와 간격(0/2/4/8/16초)을 둘이 같이 센다
    - 런처가 끌 때는 stopping 을 먼저 켜서, 워치독이 되살리지 않게 한다

런처는 자기가 띄운 것은 Popen 으로, 워치독이 띄운 것은 PID+생성 시각으로 지켜본다.
주기 실행(ONESHOT)은 "죽어서 되살리는 것" 이 아니라 원래 주기적으로 도는 검사다.
다만 비정상 종료하면 다음 주기에 다시 부르고, 같은 한도를 넘으면 멈춘다.

## 출력은 모듈마다 파일로 뺀다

모듈 7개가 한 콘솔에 같이 찍으면 읽을 수 없고, 무엇보다 파이프가 가득 차면
자식 프로세스가 멈춘다(Windows 파이프 버퍼는 몇 KB뿐이다). 그래서 각자
`client/Launcher/logs/<모듈>.log` 로 보낸다. 워치독이 되살려도 같은 파일에 이어 쓴다.
"""

import ctypes
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import registry
from modules import CONTINUOUS, ONESHOT, REPO, Module

LOG_DIR = registry.LOG_DIR

# 안티치트 자신이 띄운 프로세스 목록 = 등록부. 1번 external_access·커널 쪽이
# "우리 프로세스" 를 알아보는 근거이고, 4번 워치독이 무엇을 지켜볼지 아는 근거다.
# 자세한 모양은 registry.py 맨 위에 있다.
PID_FILE = registry.PID_FILE

# 상태값. ui.py 가 이걸 보고 화면을 그린다.
MISSING = "MISSING"        # 파일이 없다 = 아직 구현 전
SKIPPED = "SKIPPED"        # 조건이 안 맞아 건너뜀 (관리자 권한 등)
PENDING = "PENDING"        # 등록됐고 아직 시작 전 (게임을 기다리는 중)
RUNNING = "RUNNING"
DONE = "DONE"              # 한 번 돌고 정상 종료
WARN = "WARN"              # 돌긴 했는데 검사가 성립하지 않음 (종료코드 2)
RESTARTING = "RESTART"     # 상주 모듈이 죽었고 되살리는 중 (간격 대기 포함). 화면 칸 9자라 짧게
FAILED = "FAILED"          # 비정상 종료. 되살리지 않거나 한도를 넘었다
STOPPED = "STOPPED"        # 우리가 끝냈다


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


@dataclass
class ModuleState:
    module: Module
    status: str = PENDING
    proc: Optional[subprocess.Popen] = None   # 런처가 띄운 것
    adopted_pid: Optional[int] = None          # 워치독이 띄워서 이어받은 것
    adopted_ctime: int = 0
    started_by: str = ""
    started_at: float = 0.0
    last_code: Optional[int] = None
    runs: int = 0
    restarts: int = 0
    skips: int = 0             # 등록부에서 못 찾은 횟수. 한 번으로 포기하지 않는다
    crash_times: List[float] = field(default_factory=list)   # ONESHOT 비정상 종료 시각
    next_run_at: float = 0.0
    detail: str = ""
    log_path: str = ""

    @property
    def name(self) -> str:
        return self.module.name

    @property
    def pid(self) -> Optional[int]:
        if self.proc is not None:
            return self.proc.pid
        return self.adopted_pid

    def snapshot(self) -> dict:
        """ui.py 로 넘기는 한 줄 요약. **여기가 동효님과의 접점이다.**"""
        return {
            "name": self.name,
            "owner": self.module.owner,
            "status": self.status,
            "mode": self.module.mode,
            "runs": self.runs,
            "restarts": self.restarts,
            "started_by": self.started_by,
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
        registry.begin_session(session)
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

    def _restartable(self, st: ModuleState) -> bool:
        return st.module.mode == CONTINUOUS and st.module.restart

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
        try:
            # 상주 모듈은 워치독과 같은 잠금 아래에서 띄운다. 워치독이 먼저 띄웠으면 이어받는다.
            with registry.lock("mod_" + name):
                live = registry.live_pid(name) if self._restartable(st) else None
                if live:
                    self._adopt(st, *live)
                    return True
                proc = registry.spawn(argv, cwd, st.log_path,
                                      note=f"launcher run #{st.runs + 1}")
                try:
                    registry.register(name, proc, by="launcher",
                                      restartable=self._restartable(st),
                                      argv=argv, cwd=cwd, log=st.log_path)
                except BaseException:
                    # 등록을 못 하면 방금 띄운 것을 남기지 않는다. 아무도 추적하지 못하고
                    # stop_all 도 모르는 프로세스가 되어 세션이 끝난 뒤에도 남는다.
                    registry._kill_proc(proc)
                    raise
        except Exception as e:
            if st.module.mode == ONESHOT and st.module.every_s:
                # 주기 검사는 일시적 실패 한 번으로 세션 끝까지 멈추면 안 된다.
                st.status = WARN
                st.detail = f"실행 실패: {e} — 다음 주기에 다시"
                st.next_run_at = time.time() + st.module.every_s
            else:
                st.status = FAILED
                st.detail = f"실행 실패: {e}"
            self.say(f"  ! {name} 실행 실패 — {e}")
            return False

        st.proc, st.adopted_pid, st.adopted_ctime = proc, None, 0
        st.status = RUNNING
        st.started_by = "launcher"
        st.started_at = time.time()
        st.runs += 1
        st.detail = ""
        return True

    def start_group(self, needs_game: bool) -> None:
        for st in self.states.values():
            if st.module.needs_game == needs_game and st.status == PENDING:
                self.start(st.name)

    def _adopt(self, st: ModuleState, pid: int, ctime: int) -> None:
        """워치독이 띄운 프로세스를 이어받는다. 새로 띄우지 않는다."""
        e = registry.entry(st.name) or {}
        st.proc = None
        st.adopted_pid, st.adopted_ctime = pid, ctime
        st.started_by = e.get("started_by", "watchdog")
        st.restarts = len(e.get("restarts", []))
        st.status = RUNNING
        st.started_at = time.time()
        st.detail = f"{st.started_by} 가 띄운 pid {pid} 를 이어받음"

    # ── 감시 ───────────────────────────────────────────────────────────
    def poll(self) -> None:
        """상태를 갱신하고, 죽은 상주 모듈을 되살리고, 주기 실행을 다시 부른다."""
        now = time.time()
        exited = False
        for st in self.states.values():
            if st.status == RUNNING:
                if st.proc is not None:
                    code = st.proc.poll()
                    if code is not None:
                        st.proc = None
                        st.last_code = code
                        exited = True
                        if st.module.mode == ONESHOT:
                            self._oneshot_exit(st, code, now)
                        else:
                            self._down(st, f"종료됨 (code {code})")
                elif st.adopted_pid and not registry.is_alive(st.adopted_pid, st.adopted_ctime):
                    st.adopted_pid, st.adopted_ctime = None, 0
                    exited = True
                    self._down(st, "이어받은 프로세스가 종료됨")

            if st.status == RESTARTING:
                self._try_restart(st)

            if (st.status in (DONE, WARN) and st.module.every_s
                    and st.next_run_at and now >= st.next_run_at):
                self.start(st.name)

        if exited:
            # 죽은 PID 를 등록부에 남겨 두지 않는다. 주기 검사는 30초에 한 번 도는데,
            # 그동안 modules 에 죽은 PID 가 있으면 1번이 엉뚱한 프로세스를 우리 것으로 본다.
            try:
                with registry.edit():
                    pass          # _save 가 살아 있는 것만으로 modules 를 다시 쓴다
            except Exception:
                pass

    def _oneshot_exit(self, st: ModuleState, code: int, now: float) -> None:
        # run_session.py 의 계약: 0 정상 / 1 의심 / 2 검사 실패
        if code in (0, 1, 2):
            st.status = {0: DONE, 1: DONE, 2: WARN}[code]
            st.detail = {0: "정상", 1: "의심 발견", 2: "검사 실패"}[code]
        else:
            # 비정상 종료. 다음 주기에 다시 부르되, 상주 모듈과 같은 한도를 넘으면 멈춘다.
            st.crash_times = [t for t in st.crash_times if now - t < registry.WINDOW_S] + [now]
            if len(st.crash_times) >= registry.MAX_RESTARTS:
                st.status = FAILED
                st.detail = (f"비정상 종료 {len(st.crash_times)}회 "
                             f"({registry.WINDOW_S / 60:.0f}분 안) — 더 부르지 않음. 로그 확인")
                self.say(f"  ! {st.name} 이 계속 비정상 종료합니다. {st.log_path}")
                return
            st.status = WARN
            st.detail = f"비정상 종료 (code {code}) — 다음 주기에 다시"
        st.next_run_at = time.time() + st.module.every_s if st.module.every_s else 0.0

    def _down(self, st: ModuleState, why: str) -> None:
        if not self._restartable(st):
            st.status = FAILED
            st.detail = f"상주 모듈이 {why} — 재시작 안 함, 로그 확인"
            self.say(f"  ! {st.name} 이 멈췄습니다. {st.log_path}")
            return
        st.status = RESTARTING
        st.detail = f"{why} — 되살리는 중"
        self._try_restart(st)

    def _try_restart(self, st: ModuleState) -> None:
        try:
            status, pid, proc = registry.restart_if_dead(st.name, by="launcher")
        except Exception as e:
            st.detail = f"재시작 시도 실패: {e}"
            return
        if status == registry.RESTARTED:
            st.proc, st.adopted_pid, st.adopted_ctime = proc, None, 0
            st.restarts = len((registry.entry(st.name) or {}).get("restarts", []))
            st.status = RUNNING
            st.started_by = "launcher"
            st.started_at = time.time()
            st.runs += 1
            st.skips = 0
            st.detail = f"런처가 되살림 ({st.restarts}/{registry.MAX_RESTARTS})"
            self.say(f"  ~ {st.name} 을 되살렸습니다 (pid {pid})")
        elif status == registry.ALIVE:
            live = registry.live_pid(st.name)
            if live:
                self._adopt(st, *live)
        elif status == registry.BACKOFF:
            st.detail = "되살리기 전 대기 중 (연속 재시작 간격)"
        elif status == registry.GAVE_UP:
            st.status = FAILED
            st.detail = (f"재시작 한도 초과 ({registry.WINDOW_S / 60:.0f}분에 "
                         f"{registry.MAX_RESTARTS}회) — 로그 확인")
            self.say(f"  ! {st.name} 을 더 되살리지 않습니다. {st.log_path}")
        elif status == registry.SKIP:
            # 등록부를 그 순간 못 읽었을 수도 있다(상대가 파일을 바꿔 끼우는 중).
            # 한 번 못 봤다고 감시를 포기하면 멀쩡한 모듈을 세션 끝까지 버리게 된다.
            st.skips += 1
            if st.skips >= 5:
                st.status = FAILED
                st.detail = "등록부에서 찾을 수 없음 (5회 확인)"
                self.say(f"  ! {st.name} 을 등록부에서 찾을 수 없습니다.")
            else:
                st.detail = f"등록부 확인 실패 {st.skips}/5 — 다시 시도"
        # STOPPING 이면 아무것도 안 한다. 곧 stop_all 이 정리한다.

    def snapshot(self) -> List[dict]:
        return [self.states[n].snapshot() for n in self.states]

    def running_count(self) -> int:
        return sum(1 for s in self.states.values() if s.status == RUNNING)

    # ── 종료 ───────────────────────────────────────────────────────────
    def stop_all(self, grace_s: float = 10.0) -> None:
        """stopping 을 설정하고 모듈이 전송 큐 등을 정리할 시간을 준 뒤 종료한다.

        **stopping 을 먼저 켠다.** 안 그러면 모듈을 끄는 사이 워치독이 "죽었다" 며
        되살린다. 그 뒤 우리가 띄운 것, 이어받은 것, 등록부에 적힌 것을 모두 끈다.

        자식은 별도 콘솔 프로세스 그룹으로 실행한다. CTRL_BREAK_EVENT 로 Python 의
        KeyboardInterrupt/finally 경로를 먼저 실행하고, 제한 시간이 지나면 강제 종료한다.
        """
        try:
            registry.set_stopping()
        except Exception as e:
            self.say(f"  ! 등록부에 종료 표시를 못 했습니다: {e}")

        # 진행 중인 재시작이 끝나기를 기다린다. 되살리는 쪽은 모듈 잠금을 쥐고 있고,
        # 끝낼 때 stopping 을 다시 본다. 안 기다리면 그 프로세스가 등록 전 상태로 남아
        # 아무도 못 끄게 된다.
        for n in self.states:
            try:
                with registry.lock("mod_" + n, timeout=8):
                    pass
            except Exception:
                pass

        for st in self.states.values():
            if st.proc is not None and st.proc.poll() is None:
                try:
                    st.proc.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    try:
                        st.proc.terminate()
                    except Exception:
                        pass
            if st.adopted_pid:
                registry.request_stop(st.adopted_pid, st.adopted_ctime)
        deadline = time.time() + grace_s
        while time.time() < deadline:
            entries = registry.load().get("entries", {}).values()
            if not any(registry.is_alive(e.get("pid"), e.get("create_time", 0)) for e in entries):
                break
            time.sleep(0.1)
        for st in self.states.values():
            if st.proc is not None:
                try:
                    st.proc.wait(timeout=max(0.0, deadline - time.time()))
                except Exception:
                    try:
                        st.proc.kill()
                        self.say(f"  - {st.name} 강제 종료")
                    except Exception:
                        pass
                st.proc = None
            if st.adopted_pid:
                if registry.is_alive(st.adopted_pid, st.adopted_ctime):
                    registry.kill(st.adopted_pid, st.adopted_ctime)
                st.adopted_pid, st.adopted_ctime = None, 0
            if st.status in (RUNNING, RESTARTING):
                st.status = STOPPED

        # 등록부에 남은 것까지 끈다. 워치독이 stopping 직전에 띄운 것이 있을 수 있다.
        for e in registry.load().get("entries", {}).values():
            registry.kill(e.get("pid"), e.get("create_time", 0))
        try:
            registry.end_session()
        except Exception:
            pass
