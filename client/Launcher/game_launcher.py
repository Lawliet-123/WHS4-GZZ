r"""게임 프로세스 찾기·실행.

> **이 파일은 동효님 담당입니다.** 런처가 돌아가야 나머지를 붙여볼 수 있어서
> 최소 동작만 먼저 채워뒀습니다. 아래 세 함수의 이름과 반환값만 지켜 주시면
> 안을 통째로 바꾸셔도 main.py 는 손댈 필요가 없습니다.
>
>     find_game_pid() -> Optional[int]
>     launch() -> bool                     실행을 시도했으면 True
>     wait_for_game(timeout_s) -> Optional[int]
>     find_game_root() -> Optional[str]    ...\MECCHA CHAMELEON
>     find_game_dir() -> str               ...\Chameleon\Binaries\Win64
>
> Steam 으로 띄울지, exe 를 직접 띄울지, UI 에서 경로를 고르게 할지는
> 동효님이 정하시면 됩니다.
>
> 9/29 에 뒤의 두 함수를 추가했습니다. 그전에는 `modules.GAME_DIR` 한 줄에 박힌
> 경로를 그대로 써서 **그 경로가 아닌 PC 에서는 게임 폴더를 알 수 없었습니다.**
> 다른 PC 에서 정상 세션을 찍기로 한 이상 먼저 풀어야 했습니다.

## 이미 떠 있는 게임을 다시 띄우지 않는다

측정할 때는 게임을 먼저 켜두고 런처를 나중에 켜는 경우가 대부분이다.
그때 런처가 게임을 또 띄우면 창이 두 개가 되거나 Steam 이 오류를 낸다.
그래서 항상 **찾아보고 없을 때만** 띄운다.
"""

import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import time
from typing import Optional

from modules import GAME_DIR, GAME_EXE

STEAM_APPID = "4704690"

TH32CS_SNAPPROCESS = 0x00000002

# 게임 폴더는 두 층으로 쓰인다. 같은 말로 부르면 서로 다른 걸 가리키게 된다.
#   루트  ...\steamapps\common\MECCHA CHAMELEON      filesystem 탐지기가 훑는 층
#   실행  ...\MECCHA CHAMELEON\Chameleon\Binaries\Win64   exe 와 UE4SS 가 있는 층
GAME_INSTALL_NAME = "MECCHA CHAMELEON"
WIN64_REL = os.path.join("Chameleon", "Binaries", "Win64")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


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


def _exe_path_of(pid: int) -> Optional[str]:
    """PID 의 실행 파일 경로. 읽기 전용 권한만 연다.

    PROCESS_QUERY_LIMITED_INFORMATION 하나면 충분하다. 권한을 더 들면
    1번 external_access 가 우리를 핵으로 잡는다(9/29 에 고친 그 문제다).
    """
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wt.HANDLE
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.CloseHandle.argtypes = [wt.HANDLE]
        k32.QueryFullProcessImageNameW.argtypes = [
            wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    except Exception:
        return None
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        n = wt.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
    finally:
        k32.CloseHandle(h)
    return None


def _steam_root() -> Optional[str]:
    try:
        import winreg
    except ImportError:
        return None
    for hive, key, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as k:
                v = winreg.QueryValueEx(k, name)[0]
        except OSError:
            continue
        if v and os.path.isdir(v):
            return v
    return None


def _steam_libraries() -> list:
    """스팀 라이브러리 폴더 전부. 게임이 D 드라이브에 깔려 있어도 찾아야 한다."""
    libs, root = [], _steam_root()
    if root:
        libs.append(root)
        try:
            with open(os.path.join(root, "steamapps", "libraryfolders.vdf"),
                      encoding="utf-8", errors="replace") as f:
                text = f.read()
            for m in re.finditer(r'"path"\s*"([^"]+)"', text):
                libs.append(m.group(1).replace("\\\\", "\\"))
        except OSError:
            pass
    out, seen = [], set()
    for p in libs:
        # vdf 는 'c:/program files (x86)/steam' 처럼 적어 두기도 한다. 글자만
        # 비교하면 레지스트리에서 얻은 같은 폴더를 두 번 훑게 된다.
        key = os.path.normcase(os.path.normpath(p))
        if os.path.isdir(p) and key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _root_in_library(lib: str) -> Optional[str]:
    """이 라이브러리 안의 게임 설치 폴더. 폴더 이름은 appmanifest 가 정답이다."""
    common = os.path.join(lib, "steamapps", "common")
    try:
        with open(os.path.join(lib, "steamapps", f"appmanifest_{STEAM_APPID}.acf"),
                  encoding="utf-8", errors="replace") as f:
            m = re.search(r'"installdir"\s*"([^"]+)"', f.read())
        if m:
            p = os.path.join(common, m.group(1))
            if os.path.isdir(p):
                return p
    except OSError:
        pass
    p = os.path.join(common, GAME_INSTALL_NAME)
    return p if os.path.isdir(p) else None


_cache = {}


def find_game_root() -> Optional[str]:
    """게임 설치 폴더(...\\MECCHA CHAMELEON). 못 찾으면 None.

    예전에는 modules.GAME_DIR 한 줄에 박힌 경로를 그냥 썼다. 그 경로가 없는
    PC 에서는 steam://rungameid 로 넘어가긴 하지만 **폴더를 아는 것 자체가
    안 됐다.** 다른 PC 에서 정상 세션을 찍기로 한 이상 이대로는 막힌다.

    찾는 순서는 확실한 것부터다.
        1. GZZ_GAME_DIR 환경변수      사람이 직접 지정한 것
        2. 떠 있는 게임 프로세스       추측이 아니라 사실
        3. 스팀 라이브러리             레지스트리 + libraryfolders.vdf
        4. modules.GAME_DIR           마지막 기본값
    """
    if "root" in _cache:
        return _cache["root"]
    got = None
    env = (os.environ.get("GZZ_GAME_DIR") or "").strip()
    if env and os.path.isdir(env):
        got = _root_of_bin(env) if os.path.basename(env).lower() == "win64" else env
    if got is None:
        pid = find_game_pid()
        if pid is not None:
            exe = _exe_path_of(pid)
            if exe:
                got = _root_of_bin(os.path.dirname(exe))
    if got is None:
        for lib in _steam_libraries():
            got = _root_in_library(lib)
            if got:
                break
    if got is None and os.path.isdir(GAME_DIR):
        got = _root_of_bin(GAME_DIR)
    _cache["root"] = got
    return got


def _root_of_bin(bin_dir: str) -> str:
    """...\\Chameleon\\Binaries\\Win64 -> ...\\MECCHA CHAMELEON (세 단계 위)."""
    p = bin_dir
    for _ in range(3):
        p = os.path.dirname(p)
    return p or bin_dir


def find_game_dir() -> str:
    """실행 파일이 있는 폴더. 못 찾으면 예전 기본값을 그대로 돌려준다.

    None 을 돌려주지 않는 이유는 호출부가 전부 경로를 기대하고 있어서다.
    찾았는지 여부는 `find_game_root() is not None` 으로 보면 된다.
    """
    if "bin" in _cache:
        return _cache["bin"]
    root = find_game_root()
    got = os.path.join(root, WIN64_REL) if root else GAME_DIR
    if not os.path.isdir(got):
        got = GAME_DIR
    _cache["bin"] = got
    return got


def launch() -> bool:
    """게임을 띄운다. 이미 떠 있으면 아무것도 안 한다."""
    if find_game_pid() is not None:
        return False

    game_dir = find_game_dir()
    exe = os.path.join(game_dir, GAME_EXE)
    if os.path.exists(exe):
        # UE4SS 는 게임 폴더에 있으므로 cwd 를 맞춰야 자동 로드된다.
        try:
            subprocess.Popen([exe], cwd=game_dir)
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
