"""게임 프로세스 찾기·실행.

> **이 파일은 동효님 담당입니다.** 런처가 돌아가야 나머지를 붙여볼 수 있어서
> 최소 동작만 먼저 채워뒀습니다. 아래 세 함수의 이름과 반환값만 지켜 주시면
> 안을 통째로 바꾸셔도 main.py 는 손댈 필요가 없습니다.
>
>     find_game_pid() -> Optional[int]
>     launch() -> bool                     실행을 시도했으면 True
>     wait_for_game(timeout_s) -> Optional[int]
>
> Steam 으로 띄울지, exe 를 직접 띄울지, UI 에서 경로를 고르게 할지는
> 동효님이 정하시면 됩니다.

## 이미 떠 있는 게임을 다시 띄우지 않는다

측정할 때는 게임을 먼저 켜두고 런처를 나중에 켜는 경우가 대부분이다.
그때 런처가 게임을 또 띄우면 창이 두 개가 되거나 Steam 이 오류를 낸다.
그래서 항상 **찾아보고 없을 때만** 띄운다.
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import time
from typing import Optional

from modules import GAME_DIR, GAME_EXE

STEAM_APPID = "4704690"

TH32CS_SNAPPROCESS = 0x00000002


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260)]


def find_game_pid() -> Optional[int]:
    """게임이 떠 있으면 PID, 아니면 None.

    ctypes Toolhelp 를 직접 쓴다. 다른 팀원 모듈을 불러오면 그쪽이 고장났을 때
    런처까지 못 뜨는데, 런처는 제일 마지막까지 살아 있어야 하는 프로그램이다.
    """
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return None
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return None
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == GAME_EXE.lower():
                return int(entry.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return None


def launch() -> bool:
    """게임을 띄운다. 이미 떠 있으면 아무것도 안 한다."""
    if find_game_pid() is not None:
        return False

    exe = os.path.join(GAME_DIR, GAME_EXE)
    if os.path.exists(exe):
        # UE4SS 는 게임 폴더에 있으므로 cwd 를 맞춰야 자동 로드된다.
        try:
            subprocess.Popen([exe], cwd=GAME_DIR)
            return True
        except Exception:
            pass
    try:
        os.startfile(f"steam://rungameid/{STEAM_APPID}")
        return True
    except Exception:
        return False


def wait_for_game(timeout_s: float = 120.0, poll_s: float = 1.0) -> Optional[int]:
    """게임이 뜰 때까지 기다린다. 떴으면 PID, 시간이 다 되면 None."""
    end = time.time() + timeout_s
    while time.time() < end:
        pid = find_game_pid()
        if pid is not None:
            return pid
        time.sleep(poll_s)
    return None
