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

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
WAIT_TIMEOUT = 0x102


def _open(pid, access):
    if not pid:
        return None
    return _k32.OpenProcess(access, False, int(pid)) or None


def _ctime_of(handle) -> int:
    c, e, k, u = (wintypes.FILETIME() for _ in range(4))
    if not _k32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e),
                                ctypes.byref(k), ctypes.byref(u)):
        return 0
    return (c.dwHighDateTime << 32) | c.dwLowDateTime


def create_time(pid) -> int:
    """프로세스 생성 시각(FILETIME 정수). 못 얻으면 0."""
    h = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
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
    """살아 있는가. ctime 을 주면 그때 그 프로세스가 맞는지까지 본다(0 이면 모름=아님)."""
    if ctime == 0:
        return False
    h = _open(pid, SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION)
    if not h:
        return False
    try:
        if _k32.WaitForSingleObject(h, 0) != WAIT_TIMEOUT:
            return False
        return ctime is None or _ctime_of(h) == ctime
    finally:
        _k32.CloseHandle(h)


def kill(pid, ctime: Optional[int] = None) -> bool:
    """그때 그 프로세스일 때만 끈다. 재사용된 PID 의 엉뚱한 프로세스는 끄지 않는다."""
    h = _open(pid, SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE)
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
    """등록부를 읽는다. 없거나 깨졌으면 빈 것으로 본다."""
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
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

def begin_session(session_id: str) -> None:
    """런처가 세션을 시작할 때 한 번. 지난 세션 기록을 지운다."""
    me = os.getpid()
    with edit() as d:
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


# ── 띄우기·등록 ─────────────────────────────────────────────────────────

def spawn(argv: List[str], cwd: str, log: str, note: str = "") -> subprocess.Popen:
    """모듈을 띄운다. 출력은 모듈 로그 파일에 이어 쓴다. 런처가 쓰던 방식 그대로다."""
    os.makedirs(os.path.dirname(log) or ".", exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n{'=' * 70}\n[{note or 'start'}] {time.strftime('%H:%M:%S')}\n"
                f"[cmd] {' '.join(argv)}\n{'=' * 70}\n")
        f.flush()
        # 자식이 핸들을 물려받으므로 여기서 닫아도 자식 출력은 계속 파일로 간다.
        return subprocess.Popen(argv, cwd=cwd, stdout=f, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env)


def register(name: str, proc: subprocess.Popen, *, by: str, restartable: bool,
             argv: List[str], cwd: str, log: str) -> None:
    """방금 띄운 프로세스를 적는다. 재시작 기록은 이어간다."""
    with edit() as d:
        prev = d.setdefault("entries", {}).get(name) or {}
        d["entries"][name] = {
            "pid": proc.pid,
            "create_time": popen_create_time(proc),
            "started_by": by,
            "restartable": bool(restartable),
            "argv": list(argv), "cwd": cwd, "log": log,
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
    recent = [t for t in e.get("restarts", []) if now - t < WINDOW_S]
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
        recent = [t for t in e.get("restarts", []) if now - t < WINDOW_S] + [now]
        proc = spawn(e["argv"], e["cwd"], e["log"],
                     note=f"{by} restart {len(recent)}/{MAX_RESTARTS}")
        with edit() as d2:
            if d2.get("stopping"):
                # 띄우는 사이 런처가 끄기 시작했다. 방금 띄운 것을 스스로 끈다.
                try:
                    proc.kill()
                except Exception:
                    pass
                return STOPPING, None, None
            e2 = d2.setdefault("entries", {}).setdefault(name, dict(e))
            e2.update({"pid": proc.pid, "create_time": popen_create_time(proc),
                       "started_by": by, "restarts": recent, "gave_up": False})
        return RESTARTED, proc.pid, proc


def _mark_gave_up(name: str) -> None:
    with edit() as d:
        e = d.get("entries", {}).get(name)
        if e and not e.get("gave_up"):
            e["gave_up"] = True
