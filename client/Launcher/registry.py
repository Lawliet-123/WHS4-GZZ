"""안티치트 프로세스 등록부. 런처와 워치독(4번 SelfDefense)이 같이 쓴다.

## 왜 있나

런처와 워치독 **둘 다** 죽은 모듈을 되살린다(2026-09-29 성민님 제안). 조율 없이 따로
되살리면 세 가지가 깨진다. 이 파일 하나로 셋을 막는다.

  1. 같은 모듈이 두 번 뜬다
     -> 모듈마다 잠금을 두고, 잠금을 잡은 쪽만 띄운다. 잠금을 잡은 **뒤에** 등록부를
        다시 읽어서, 상대가 이미 띄웠으면 새로 띄우지 않고 그것을 이어받는다.
  2. 워치독이 띄운 PID 를 아무도 모른다
     -> 누가 띄우든 여기 적는다. anticheat_pids.json 이 곧 등록부다. 1번·커널 쪽이
        "우리 프로세스" 를 알아보는 근거가 이 파일이라, 빠지면 우리를 다시 잡는다.
  3. 런처가 일부러 끄는 모듈을 워치독이 되살린다
     -> 끌 때 stopping 을 먼저 켠다. 되살리는 쪽은 띄운 뒤 등록하기 직전에 이걸 한 번
        더 보고, 켜져 있으면 방금 띄운 것을 스스로 끈다.
     -> 런처가 비정상으로 죽어 stopping 을 못 켠 경우도 있다. 그때는 등록부의 런처
        PID 가 죽어 있으므로 되살리지 않고 orphaned 를 돌려준다.

재시작 횟수도 여기서 같이 센다. 둘이 따로 세면 한도가 두 배가 된다.

## 워치독에서 쓰는 법

표준 라이브러리만 쓴다. 이 파일 하나만 가져다 쓰면 된다.

    sys.path.insert(0, r"<레포>/client/Launcher")
    import registry
    for name in registry.restartable_names():
        status, pid, _ = registry.restart_if_dead(name, by="watchdog")

런처도 똑같이 restart_if_dead 를 부른다. 같은 함수를 쓰니 규칙이 어긋날 수 없다.
되살리는 대상은 상주 모듈(CONTINUOUS)뿐이다. 한 번 돌고 끝나는 주기 검사(ONESHOT)는
끝나는 게 정상이라 restartable=False 로 적히고, 워치독은 건드리지 않는다.

## 잠금

msvcrt 바이트 잠금을 쓴다. 잠금을 잡은 프로세스가 죽으면 OS 가 풀어준다. 그래서
"주인이 죽어 영원히 안 풀리는 잠금" 을 따로 치울 필요가 없다.

잠금 순서는 항상 모듈 잠금 -> 등록부 잠금이다. 반대로 잡는 곳이 없어서 서로 기다리다
멈추는 일이 없다.

## 등록부 파일 모양 (anticheat_pids.json)

    {
      "launcher_pid": 1234, "launcher_create_time": ..., "session_id": "run_001",
      "stopping": false,
      "modules": {"external_access": 5678},        <- 예전 형식. 살아 있는 것만. 그대로 둔다
      "entries": {
        "external_access": {
          "pid": 5678, "create_time": ..., "started_by": "launcher" | "watchdog",
          "restartable": true, "argv": [...], "cwd": "...", "log": "...",
          "restarts": [재시작 시각, ...], "gave_up": false
        }
      },
      "policy": {"max_restarts": 5, "window_s": 300}
    }

create_time 은 PID 재사용을 가려낸다. 같은 PID 라도 생성 시각이 다르면 다른 프로세스다.
"""

import contextlib
import ctypes
import json
import msvcrt
import os
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
# 시험할 때는 다른 폴더를 쓴다. 실행 중인 런처의 등록부를 건드리지 않게.
LOG_DIR = os.environ.get("AC_LAUNCHER_LOG_DIR") or os.path.join(HERE, "logs")
PID_FILE = os.path.join(LOG_DIR, "anticheat_pids.json")
LOCK_DIR = os.path.join(LOG_DIR, "locks")

# 재시작 규칙. 런처·워치독이 같은 값을 쓴다.
MAX_RESTARTS = 5               # WINDOW_S 안에 이만큼까지 되살린다
WINDOW_S = 300.0
BACKOFF_S = (0, 2, 4, 8, 16)   # n 번째 재시작 전, 직전 재시작에서 최소 이만큼 지나야 한다

# restart_if_dead 가 돌려주는 상태
ALIVE = "alive"            # 살아 있다. 상대가 이미 되살렸을 수도 있다
RESTARTED = "restarted"    # 내가 되살렸다
BACKOFF = "backoff"        # 직전 재시작이 너무 최근이다. 다음에 다시 부르면 된다
GAVE_UP = "gave_up"        # 한도를 넘었다. 더 되살리지 않는다
STOPPING = "stopping"      # 런처가 끄는 중이다
ORPHANED = "orphaned"      # 런처가 없다(비정상 종료 또는 누가 죽였다). 되살리지 않는다
SKIP = "skip"              # 등록 안 됐거나 되살리는 대상이 아니다

# ── 프로세스 생존 확인 ──────────────────────────────────────────────────

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)
_k32.CloseHandle.restype = wintypes.BOOL
_k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
_k32.GetProcessTimes.restype = wintypes.BOOL
_k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
_k32.TerminateProcess.restype = wintypes.BOOL
_k32.GenerateConsoleCtrlEvent.argtypes = (wintypes.DWORD, wintypes.DWORD)
_k32.GenerateConsoleCtrlEvent.restype = wintypes.BOOL

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
WAIT_TIMEOUT = 0x102
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87       # 그런 PID 가 없을 때 나온다
CTRL_BREAK_EVENT = 1


def _open(pid, access):
    """(핸들, 오류코드). 못 열었을 때 '없다' 와 '권한이 없다' 를 가르려고 코드도 준다."""
    if not pid:
        return None, ERROR_INVALID_PARAMETER
    ctypes.set_last_error(0)
    h = _k32.OpenProcess(access, False, int(pid))
    return (h or None), (0 if h else ctypes.get_last_error())


def _ctime_of(handle) -> int:
    c, e, k, u = (wintypes.FILETIME() for _ in range(4))
    if not _k32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e),
                                ctypes.byref(k), ctypes.byref(u)):
        return 0
    return (c.dwHighDateTime << 32) | c.dwLowDateTime


def create_time(pid) -> int:
    """프로세스 생성 시각(FILETIME 정수). 못 얻으면 0."""
    h, _err = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not h:
        return 0
    try:
        return _ctime_of(h)
    finally:
        _k32.CloseHandle(h)


def popen_create_time(proc: subprocess.Popen) -> int:
    """Popen 이 쥔 핸들에서 생성 시각을 읽는다. 곧바로 죽은 프로세스도 읽힌다.

    PID 로 다시 열면 그 사이 죽은 프로세스는 못 연다. 그러면 생성 시각 없이 PID 만
    남고, 나중에 그 PID 를 엉뚱한 프로세스가 재사용하면 우리 것으로 오인한다.
    """
    try:
        return _ctime_of(int(proc._handle))    # CPython Windows 의 Popen 핸들
    except Exception:
        return create_time(proc.pid)


def is_alive(pid, ctime: Optional[int] = None) -> bool:
    """살아 있는가. ctime 을 주면 그때 그 프로세스가 맞는지까지 본다(0 이면 모름=아님).

    **권한이 없어서 못 연 것은 '죽었다' 가 아니다.** 4번 SelfDefense 처럼 자기를
    보호하려고 핸들 접근을 막는 모듈이 나오면, 죽은 것으로 보고 한도까지 중복으로
    띄우게 된다. 이때는 살아 있다고 본다. 대신 PID 재사용은 가려내지 못한다.
    """
    if ctime == 0:
        return False
    h, err = _open(pid, SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION)
    if not h:
        return err == ERROR_ACCESS_DENIED
    try:
        if _k32.WaitForSingleObject(h, 0) != WAIT_TIMEOUT:
            return False
        return ctime is None or _ctime_of(h) == ctime
    finally:
        _k32.CloseHandle(h)


def kill(pid, ctime: Optional[int] = None) -> bool:
    """그때 그 프로세스일 때만 끈다. 재사용된 PID 의 엉뚱한 프로세스는 끄지 않는다."""
    h, _err = _open(pid, SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE)
    if not h:
        return False
    try:
        if _k32.WaitForSingleObject(h, 0) != WAIT_TIMEOUT:
            return False
        if ctime is not None and _ctime_of(h) != ctime:
            return False
        return bool(_k32.TerminateProcess(h, 1))
    finally:
        _k32.CloseHandle(h)


def request_stop(pid, ctime: Optional[int] = None) -> bool:
    """그때 그 프로세스 그룹에 CTRL_BREAK_EVENT를 보내 정상 종료를 요청한다.

    **요청이지 강제가 아니다. 받는 쪽이 처리해야 정리 코드가 돈다.** 파이썬은
    Ctrl+Break(SIGBREAK)를 기본으로 KeyboardInterrupt 로 바꾸지 않는다 — 기본 처리는
    그 자리에서 프로세스를 끝내고(종료 코드 0xC000013A) finally·atexit 이 안 돈다.
    실제로 재 봤다(9/30, 한 줄 없는 모듈은 finally 가 안 돌았다). 그래서 모듈은
    시작부에 이 한 줄이 있어야 한다.

        signal.signal(signal.SIGBREAK, signal.default_int_handler)

    이 줄이 없으면 예전 강제 종료와 같다. 나빠지는 모듈은 없지만 좋아지지도 않는다.

    못 보내는 경우(False): 이미 죽었거나 PID 가 재사용됐을 때(ctime 불일치), 런처가
    콘솔 없이 떠 있거나(pythonw, 창 모드 exe) 대상이 다른 콘솔에 붙어 있을 때.
    """
    if not pid or (ctime is not None and not is_alive(pid, ctime)):
        return False
    # spawn() creates a new console process group whose ID is the leader PID.
    return bool(_k32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, int(pid)))


# ── 잠금 ────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def lock(name: str, timeout: float = 15.0):
    """프로세스 사이 잠금. 잡은 프로세스가 죽으면 OS 가 푼다."""
    os.makedirs(LOCK_DIR, exist_ok=True)
    f = open(os.path.join(LOCK_DIR, name + ".lock"), "a+b")
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"잠금 대기 초과: {name}")
                time.sleep(0.02)
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        f.close()


# ── 등록부 읽기·쓰기 ────────────────────────────────────────────────────

def load() -> dict:
    """등록부를 읽는다. 파일이 없으면 빈 것으로 본다.

    상대가 os.replace 로 바꿔 끼우는 순간에 열면 PermissionError 가 난다.
    이걸 '비어 있음' 으로 처리하면 살아 있는 모듈을 등록 안 된 것으로 오해해서
    감시를 포기한다. 그래서 잠깐 뒤 다시 읽는다.
    """
    for _ in range(25):
        try:
            with open(PID_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            time.sleep(0.02)
    return {}


def _save(d: dict) -> None:
    # 예전 형식(modules: 이름 -> pid)도 같이 채운다. 이걸 읽는 쪽이 있을 수 있다.
    d["modules"] = {n: e["pid"] for n, e in d.get("entries", {}).items()
                    if is_alive(e.get("pid"), e.get("create_time", 0))}
    d.setdefault("note", "PIDs spawned by the anti-cheat itself (launcher or watchdog)")
    os.makedirs(LOG_DIR, exist_ok=True)
    tmp = f"{PID_FILE}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    # 다 쓴 뒤 한 번에 바꾼다. 읽는 쪽이 쓰다 만 파일을 보지 않게.
    # 누가 그 순간 파일을 열고 있으면 Windows 에서 바꾸기가 실패하므로 몇 번 다시 한다.
    for i in range(50):
        try:
            os.replace(tmp, PID_FILE)
            return
        except PermissionError:
            time.sleep(0.02)
    os.replace(tmp, PID_FILE)


@contextlib.contextmanager
def edit():
    """등록부를 읽고 고쳐 쓴다. 쓰는 쪽이 둘(런처·워치독)이라 잠금 없이는 서로 덮어쓴다."""
    with lock("_registry"):
        d = load()
        yield d
        _save(d)


def entry(name: str) -> Optional[dict]:
    return load().get("entries", {}).get(name)


def restartable_names() -> List[str]:
    return [n for n, e in load().get("entries", {}).items() if e.get("restartable")]


def live_pid(name: str) -> Optional[Tuple[int, int]]:
    """지금 살아 있는 그 모듈의 (pid, 생성 시각). 없으면 None."""
    e = entry(name)
    if e and is_alive(e.get("pid"), e.get("create_time", 0)):
        return e["pid"], e["create_time"]
    return None


# ── 세션 ────────────────────────────────────────────────────────────────

class LauncherAlreadyRunning(RuntimeError):
    """런처가 이미 하나 돌고 있다."""


def begin_session(session_id: str) -> None:
    """런처가 세션을 시작할 때 한 번. 지난 세션 기록을 지운다.

    두 가지를 먼저 처리한다.

      1. **런처가 이미 돌고 있으면 시작하지 않는다.** 그냥 지우면 두 번째 런처가
         등록부를 통째로 가져가고, 첫 런처는 자기 모듈을 잃고 재시작도 못 한다.
         나중에 한쪽을 끄면 다른 쪽 모듈까지 꺼진다.
      2. **지난 런처가 비정상 종료했으면 그 세션 모듈을 끄고 시작한다.** 기록만
         지우면 아무도 못 끄는 프로세스가 남아 다음 세션과 겹치고, 등록부에도
         없으니 1번이 우리 프로세스를 핵으로 신고한다.
    """
    me = os.getpid()
    with edit() as d:
        prev, prev_ct = d.get("launcher_pid"), d.get("launcher_create_time")
        if prev and prev != me and is_alive(prev, prev_ct or None):
            raise LauncherAlreadyRunning(
                f"런처가 이미 실행 중입니다 (pid {prev}). "
                "두 개를 같이 띄우면 서로의 모듈을 죽입니다.")
        left = [n for n, e in (d.get("entries") or {}).items()
                if kill(e.get("pid"), e.get("create_time", 0))]
        if left:
            # stderr 로 보낸다. 이 프로젝트는 사람이 보는 출력과 기계가 읽는 출력을
            # 섞지 않는다(ui.py 참고). 런처를 감싸 쓰는 쪽의 stdout 을 더럽히면 안 된다.
            print(f"  지난 세션에서 남은 모듈을 정리했습니다: {', '.join(left)}",
                  file=sys.stderr, flush=True)
        d.clear()
        d.update({
            "launcher_pid": me,
            "launcher_create_time": create_time(me),
            "session_id": session_id,
            "stopping": False,
            "policy": {"max_restarts": MAX_RESTARTS, "window_s": WINDOW_S,
                       "backoff_s": list(BACKOFF_S)},
            "entries": {},
        })


def set_stopping() -> None:
    """런처가 모듈을 끄기 **전에** 부른다. 이 뒤로는 아무도 되살리지 않는다."""
    with edit() as d:
        d["stopping"] = True


def is_stopping() -> bool:
    return bool(load().get("stopping"))


def clear_entries() -> None:
    with edit() as d:
        d["entries"] = {}


def end_session() -> None:
    """세션이 끝났다. 목록을 비우고 런처 표시도 지운다.

    런처 표시를 남겨 두면, 같은 프로세스가 런처를 다시 돌릴 때 자기 자신 때문에
    '이미 실행 중' 으로 막힌다. 끝난 세션의 런처는 주인이 아니다.
    """
    with edit() as d:
        d["entries"] = {}
        d["launcher_pid"] = None
        d["launcher_create_time"] = None


# ── 띄우기·등록 ─────────────────────────────────────────────────────────

def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _direct_python(argv: List[str], env: Dict[str, str]) -> List[str]:
    """venv 파이썬이면 중간 실행기를 건너뛰고 실제 파이썬을 바로 띄운다.

    Windows venv 의 Scripts\\python.exe 는 진짜 파이썬을 자식으로 한 번 더 띄우는
    중간 실행기다. 그대로 띄우면 등록부 pid(=Popen.pid)는 중간 실행기이고, 검사 코드는
    그 자식 PID 에서 돈다(10/6 실측: 등록 20268 / 실제 24388). 등록부로 "우리 프로세스" 를
    알아보는 쪽(4번 AntiDebug·1번·커널)이 실제 검사 프로세스를 못 본다. 시스템 파이썬으로
    런처를 돌리면 둘이 같아서 지금까지 안 드러났다.

    표준 라이브러리 multiprocessing 이 Windows venv 에서 쓰는 방법과 같다. 실제 파이썬
    (sys._base_executable)을 띄우고 __PYVENV_LAUNCHER__ 로 어느 venv 인지 알려 준다.
    자식의 sys.executable·sys.prefix·venv 패키지는 지금과 같고, 파이썬이 시작하면서 이
    변수를 지우므로 손자에게 새지 않는다(10/6 실측). 등록부 모양은 그대로다 — 적히는
    pid 만 실제 검사 프로세스로 바뀐다.
    """
    if os.name != "nt" or not argv or sys.prefix == sys.base_prefix:
        return argv                    # venv 가 아니면 중간 실행기도 없다
    base = getattr(sys, "_base_executable", "") or ""
    if not base or _same_path(base, sys.executable) or not os.path.isfile(base):
        return argv                    # 실제 파이썬을 못 찾으면 지금처럼 띄운다
    if not _same_path(argv[0], sys.executable):
        return argv                    # 우리 파이썬이 아닌 것(게임 등)은 그대로 띄운다
    env["__PYVENV_LAUNCHER__"] = sys.executable
    return [base] + list(argv[1:])


def spawn(argv: List[str], cwd: str, log: str, note: str = "",
          env_extra: Optional[Dict[str, str]] = None) -> subprocess.Popen:
    """모듈을 띄운다. 출력은 모듈 로그 파일에 이어 쓴다.

    **모듈마다 프로세스 그룹을 따로 만든다(CREATE_NEW_PROCESS_GROUP).** 그래야
    끌 때 모듈 하나만 골라 종료 신호(Ctrl+Break)를 보낼 수 있다. 같은 그룹에 두면
    신호가 런처를 포함한 전부에게 한꺼번에 간다.

    덤으로 사용자가 런처 창에서 누른 Ctrl+C 가 모듈에 바로 가지 않는다. 예전에는
    Ctrl+C 한 번에 모두가 동시에 정리를 시작했고, 사용자가 한 번 더 누르면 모듈의
    finally 가 중간에 끊겼다. 이제 런처가 받아서 순서대로 끈다.
    """
    os.makedirs(os.path.dirname(log) or ".", exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    # 중앙 전송 대기열(shared outbox)은 모듈마다 따로 준다. 안 주면 모듈들이
    # ClientConfig 기본값인 cwd(=레포 루트)/telemetry-outbox/ 하나를 같이 쓰는데,
    # shared 는 한 대기열에 보내는 프로세스 하나만 허용해서(잠금 timeout=0) 나중에
    # 뜬 모듈은 중앙 전송만 조용히 꺼진 채 돈다. 레포 안에 파일이 생기는 것도 막는다.
    # 전역으로 설정돼 있어도 덮어쓴다 — 전역 하나를 나눠 쓰면 같은 문제다.
    # 같은 모듈을 되살리면 같은 경로라 못 보낸 건이 이어서 나간다(잠금은 OS 가
    # 프로세스가 죽을 때 푼다).
    name = os.path.splitext(os.path.basename(log))[0]
    env["GZZ_TELEMETRY_OUTBOX"] = os.path.join(
        os.path.dirname(os.path.abspath(log)), "outbox", name, "client.sqlite3")
    # 하트비트는 런처 한 곳에서만 보낸다(launcher_heartbeat.py). 자식에게 주소·토큰을
    # 물려주면 input_signature(yara_scanner)가 MECCHA_TELEMETRY_HEARTBEAT_URL 을 기본값으로
    # 읽어 따로 보낸다 — 계약 문서(TELEMETRY_CONTRACT.md)가 피하라고 한 중복 발신이다.
    for k in ("MECCHA_TELEMETRY_HEARTBEAT_URL", "MECCHA_HEARTBEAT_TOKEN"):
        env.pop(k, None)
    # 모듈마다 따로 주는 환경변수(Module.env). 전부에 넣지 않는 이유: 레포 루트를
    # 모든 모듈의 PYTHONPATH 에 넣으면 최상위 modules/·server/ 같은 이름이 다른
    # 모듈의 import 를 가로챌 수 있다. PYTHONPATH 는 사용자가 이미 준 값을 지우지 않는다.
    for k, v in (env_extra or {}).items():
        if k == "PYTHONPATH" and env.get(k):
            v = v + os.pathsep + env[k]
        env[k] = v
    # venv 면 중간 실행기 없이 띄운다. 그래야 Popen.pid(=등록부 pid)가 실제 검사 프로세스다.
    # 등록부에는 원래 argv 를 적는다(register). 되살릴 때 이 함수를 다시 거치며 또 바꾼다.
    run_argv = _direct_python(argv, env)
    venv_note = (f"[venv] 중간 실행기 없이 실제 파이썬으로 띄움 ({sys.executable})\n"
                 if run_argv is not argv else "")
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n{'=' * 70}\n[{note or 'start'}] {time.strftime('%H:%M:%S')}\n"
                f"[cmd] {' '.join(run_argv)}\n{venv_note}{'=' * 70}\n")
        f.flush()
        # 자식이 핸들을 물려받으므로 여기서 닫아도 자식 출력은 계속 파일로 간다.
        return subprocess.Popen(
            run_argv,
            cwd=cwd,
            stdout=f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )


def register(name: str, proc: subprocess.Popen, *, by: str, restartable: bool,
             argv: List[str], cwd: str, log: str,
             env: Optional[Dict[str, str]] = None) -> None:
    """방금 띄운 프로세스를 적는다. 재시작 기록은 이어간다.

    `env` 는 Module.env 다. 되살리는 쪽(런처·워치독)이 같은 환경으로 띄우도록 같이 적는다.
    """
    with edit() as d:
        prev = d.setdefault("entries", {}).get(name) or {}
        d["entries"][name] = {
            "pid": proc.pid,
            "create_time": popen_create_time(proc),
            "started_by": by,
            "restartable": bool(restartable),
            "argv": list(argv), "cwd": cwd, "log": log,
            "env": dict(env or {}),
            "restarts": prev.get("restarts", []),
            "gave_up": False,
        }


# ── 되살리기 ────────────────────────────────────────────────────────────

def _decide(d: dict, name: str, now: float):
    e = d.get("entries", {}).get(name)
    if not e or not e.get("restartable"):
        return SKIP, None
    if d.get("stopping"):
        return STOPPING, None
    if is_alive(e.get("pid"), e.get("create_time", 0)):
        return ALIVE, e["pid"]
    # 런처가 비정상으로 죽으면 stopping 이 안 켜진다. 그대로 두면 워치독이 주인 없는
    # 모듈을 끝없이 되살린다. 런처가 없으면 되살리지 않고, 워치독은 이걸 보고한다
    # (누가 런처를 죽였다면 그 자체가 변조 신호다).
    lp, lct = d.get("launcher_pid"), d.get("launcher_create_time")
    if lp and not is_alive(lp, lct or None):
        return ORPHANED, None
    if e.get("gave_up"):
        return GAVE_UP, None
    # 0 <= 도 같이 본다. 시계가 뒤로 가면 미래 시각이 남는데, 그대로 세면
    # 한도에 걸린 채로 영원히 안 풀린다.
    recent = [t for t in e.get("restarts", []) if 0 <= now - t < WINDOW_S]
    if len(recent) >= MAX_RESTARTS:
        return GAVE_UP, None
    if recent and now - recent[-1] < BACKOFF_S[min(len(recent), len(BACKOFF_S) - 1)]:
        return BACKOFF, None
    return None, None          # 죽었고, 되살려도 된다


def restart_if_dead(name: str, by: str) -> Tuple[str, Optional[int], Optional[subprocess.Popen]]:
    """죽었으면 되살린다. 런처와 워치독이 **같은 함수**를 부른다.

    (상태, pid, Popen) 을 돌려준다. Popen 은 내가 되살렸을 때만 있다.
    """
    status, pid = _decide(load(), name, time.time())    # 잠금 없이 먼저 싸게 본다
    if status is not None:
        if status == GAVE_UP:
            _mark_gave_up(name)
        return status, pid, None

    with lock("mod_" + name):
        # 잠금을 잡은 뒤 다시 본다. 기다리는 사이 상대가 방금 되살렸을 수 있다.
        d = load()
        now = time.time()
        status, pid = _decide(d, name, now)
        if status is not None:
            if status == GAVE_UP:
                _mark_gave_up(name)
            return status, pid, None

        e = d["entries"][name]
        recent = [t for t in e.get("restarts", []) if 0 <= now - t < WINDOW_S] + [now]

        # 띄우기 **전에** 이번 시도를 먼저 적는다. 아래 등록이 실패해도 이 시도가
        # 한도·간격에 잡혀야 한다. 안 그러면 등록부를 못 쓰는 동안 poll 마다
        # 계속 새로 띄워서 같은 모듈이 쌓인다.
        with edit() as d0:
            if d0.get("stopping"):
                return STOPPING, None, None
            d0.setdefault("entries", {}).setdefault(name, dict(e))["restarts"] = recent

        proc = spawn(e["argv"], e["cwd"], e["log"],
                     note=f"{by} restart {len(recent)}/{MAX_RESTARTS}",
                     env_extra=e.get("env"))
        try:
            with edit() as d2:
                if d2.get("stopping"):
                    # 띄우는 사이 런처가 끄기 시작했다. 방금 띄운 것을 스스로 끈다.
                    _kill_proc(proc)
                    return STOPPING, None, None
                e2 = d2.setdefault("entries", {}).setdefault(name, dict(e))
                e2.update({"pid": proc.pid, "create_time": popen_create_time(proc),
                           "started_by": by, "restarts": recent, "gave_up": False})
        except BaseException:
            # 등록을 못 했으면 방금 띄운 것을 남기면 안 된다. 아무도 추적하지 못하고
            # stop_all 도 모르는 프로세스가 되어 세션이 끝난 뒤에도 게임 핸들을 쥔 채 남는다.
            _kill_proc(proc)
            raise
        return RESTARTED, proc.pid, proc


def _kill_proc(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass


def _mark_gave_up(name: str) -> None:
    # 이미 표시돼 있으면 아무것도 하지 않는다. 워치독은 포기한 모듈에도 계속 물어보는데,
    # 그때마다 등록부를 다시 쓰면 읽는 쪽(1번·커널)이 내내 쓰다 만 파일과 부딪힌다.
    if (load().get("entries", {}).get(name) or {}).get("gave_up"):
        return
    with edit() as d:
        e = d.get("entries", {}).get(name)
        if e and not e.get("gave_up"):
            e["gave_up"] = True
