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

import argparse               # 설치·실게임 로드 검증을 직접 시험할 명령을 받는다.
import ctypes                 # 외부 패키지 없이 Windows 프로세스 조회 API를 호출한다.
import ctypes.wintypes as wt  # Windows HANDLE·DWORD 등의 정확한 크기를 사용한다.
from dataclasses import dataclass  # 설치 결과를 상태와 설명으로 묶어 돌려준다.
import hashlib                # ZIP 및 설치 파일의 SHA-256을 계산한다.
import json                   # 사용자가 선택한 게임 경로를 다음 실행용으로 저장한다.
import os                     # 경로, 환경변수, Steam 실행에 필요한 OS 기능을 쓴다.
from pathlib import Path      # 설치 대상 파일과 ZIP 파일 경로를 조립한다.
import re                     # Steam 설정과 mods.txt의 필요한 항목을 찾는다.
import stat                   # ZIP 안의 심볼릭 링크를 구분한다.
import subprocess             # 게임 exe를 직접 실행한다.
import sys                    # 대화형 콘솔 여부를 확인한다.
import tempfile               # 설정 파일 수정 시 임시 파일을 거쳐 교체한다.
import time                   # 게임·UE4SS 로그가 준비될 때까지 기다린다.
from typing import Optional  # 발견 실패 시 None을 반환하는 계약을 표시한다.
import zipfile                # 팀이 고정한 UE4SS ZIP에서 필요한 파일만 읽는다.

from modules import GAME_DIR, GAME_EXE  # 팀 등록표와 동일한 게임 이름·기본 경로를 쓴다.

STEAM_APPID = "4704690"  # Steam URL과 appmanifest 파일명에 쓰는 게임 ID다.

TH32CS_SNAPPROCESS = 0x00000002  # Toolhelp에 프로세스 목록만 요청하는 플래그다.

# 게임 폴더는 두 층으로 쓰인다. 같은 말로 부르면 서로 다른 걸 가리키게 된다.
#   루트  ...\steamapps\common\MECCHA CHAMELEON      filesystem 탐지기가 훑는 층
#   실행  ...\MECCHA CHAMELEON\Chameleon\Binaries\Win64   exe 와 UE4SS 가 있는 층
GAME_INSTALL_NAME = "MECCHA CHAMELEON"  # appmanifest가 없을 때 찾을 설치 폴더명이다.
WIN64_REL = os.path.join("Chameleon", "Binaries", "Win64")  # 설치 루트에서 exe까지의 상대경로다.
# 실행 파일을 Program Files에 배포해도 사용자 선택을 저장할 수 있는 위치.
_user_data = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.dirname(__file__), "logs")  # 사용자별 쓰기 가능한 저장소를 고른다.
SAVED_GAME_PATH = os.path.join(_user_data, "MecchaAntiCheat", "game_path.json")  # 한 번 선택한 게임 위치다.

# 팀이 동작 확인한 ZIP의 SHA-256은 받은 원본 파일과 팀 문서에서 대조했다.
# 압축 해제 폴더의 묶음 지문은 아직 확정되지 않았다. 별도 시그니처는 선택 사항이다.
UE4SS_VERSION = "v3.0.1 Beta #0 - Git SHA #f6d5f942"  # 팀에서 동작을 확인한 런타임 빌드다.
PINNED_UE4SS_ZIP_SHA256 = "050948bdf6b4aae2ff8d834aaebadbf7535d4cb8478fbb579966a5ca3142f86a"  # g35d1795d 배포 ZIP 전체 해시다.
PINNED_UE4SS_DIRECTORY_SHA256 = ""  # 폴더 배포본은 설치 대상 파일의 묶음 지문을 따로 고정한다.
UE4SS_BUNDLE_ENV = "GZZ_UE4SS_BUNDLE"  # 배포된 ZIP의 위치를 받는 환경변수다.
PINNED_SIGNATURE_SHA256 = ""  # 별도 시그니처를 명시적으로 쓸 때만 고정 해시를 설정한다.
SIGNATURE_ENV = "GZZ_UE4SS_SIGNATURE"  # 선택적으로 제공한 시그니처 파일 위치를 받는다.

# ZIP 루트가 한 단계 더 감싸져 있거나 UE4SS 본체가 루트/ue4ss 아래에 있는
# 두 배치를 허용한다. 대상 경로는 항상 팀이 합의한 Win64 배치로 고정된다.
UE4SS_BUNDLE_FILES = {
    "dwmapi.dll": ("dwmapi.dll",),  # 프록시 DLL은 게임 exe와 같은 폴더에 둔다.
    "ue4ss/UE4SS.dll": ("ue4ss/UE4SS.dll", "UE4SS.dll"),  # 본체는 팀 배치에 맞춰 ue4ss 아래에 둔다.
    "ue4ss/UE4SS-settings.ini": ("ue4ss/UE4SS-settings.ini", "UE4SS-settings.ini"),  # 팀 설정을 설치한다.
    "ue4ss/Mods/shared/UEHelpers/UEHelpers.lua": (
        "ue4ss/Mods/shared/UEHelpers/UEHelpers.lua",  # 이미 ue4ss 폴더가 있는 ZIP 형태다.
        "Mods/shared/UEHelpers/UEHelpers.lua"),  # ZIP 루트에 Mods가 있는 형태도 받는다.
}
TEAM_MOD_FILES = {
    "ue4ss/Mods/DamageLogger/Scripts/main.lua":
        "ue4ss/Mods/DamageLogger/Scripts/main.lua",  # 에임봇 관측용 Lua의 client 내부 위치다.
    "ue4ss/Mods/GZZPaintObserver/Scripts/main.lua":
        "ue4ss/Mods/GZZPaintObserver/Scripts/main.lua",  # 페인트 관측 모드의 진입점이다.
    "ue4ss/Mods/GZZPaintObserver/Scripts/observer.lua":
        "ue4ss/Mods/GZZPaintObserver/Scripts/observer.lua",  # 페인트 이벤트 수집 로직이다.
    "ue4ss/Mods/NoclipLogger/Scripts/main.lua":
        "ue4ss/Mods/NoclipLogger/Scripts/main.lua",  # 노클립 관측용 Lua다.
    "ue4ss/Mods/GodModeTelemetry/Scripts/main.lua":
        "detectors/godmode/telemetry_mod/GodModeTelemetry/Scripts/main.lua",  # 핵 PoC가 아닌 갓모드 관측용 Lua다.
}
TEAM_MODS = ("DamageLogger", "GZZPaintObserver", "NoclipLogger", "GodModeTelemetry")  # mods.txt에서 켤 관측 모드 네 개다.
MAX_BUNDLE_MEMBER = 100 * 1024 * 1024  # ZIP의 비정상적으로 큰 단일 항목은 거부한다.

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000  # 실행 경로 조회에 필요한 최소 프로세스 권한이다.


class _PROCESSENTRY32W(ctypes.Structure):
    # Windows PROCESSENTRY32W 구조체의 필드 순서·크기가 API 계약과 맞아야 한다.
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
    try:  # Windows API를 불러올 수 없는 환경에서는 게임이 없는 것으로 취급한다.
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # 프로세스 목록 조회 함수가 있는 DLL이다.
    except Exception:  # 비-Windows 환경이나 DLL 로드 실패는 런처 전체 오류로 만들지 않는다.
        return None  # 호출 계약에 따라 미발견을 돌려준다.
    # HANDLE은 64비트 포인터다. 기본 c_int 반환형으로 받으면 값이 잘려서
    # 게임이 실행 중이어도 프로세스 스냅샷을 읽지 못할 수 있다.
    k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]  # 플래그와 PID 인자 형식을 고정한다.
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE  # 64비트 스냅샷 핸들을 잘림 없이 받는다.
    k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]  # 첫 행 API 형식이다.
    k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]  # 다음 행 API 형식이다.
    k32.CloseHandle.argtypes = [wt.HANDLE]  # 스냅샷을 닫을 때 핸들 크기를 유지한다.
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)  # 실행 중인 프로세스 스냅샷이다.
    if snap == ctypes.c_void_p(-1).value:  # Windows가 반환한 INVALID_HANDLE_VALUE를 확인한다.
        return None  # 스냅샷이 없으면 게임 PID를 추측하지 않는다.
    try:  # 어떤 경로로 함수를 끝내든 핸들은 아래 finally에서 닫는다.
        entry = _PROCESSENTRY32W()  # API가 채워 줄 프로세스 정보 버퍼다.
        entry.dwSize = ctypes.sizeof(entry)  # WinAPI가 버퍼 구조체 버전을 판별하는 값이다.
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))  # 목록의 첫 프로세스를 읽는다.
        while ok:  # 더 읽을 프로세스가 있을 때까지 순회한다.
            if entry.szExeFile.lower() == GAME_EXE.lower():  # 파일명은 대소문자를 구분하지 않는다.
                return int(entry.th32ProcessID)  # 발견한 게임의 PID를 반환한다.
            ok = k32.Process32NextW(snap, ctypes.byref(entry))  # 다음 프로세스 정보를 덮어쓴다.
    finally:  # 중간에 게임을 찾아 반환해도 실행된다.
        k32.CloseHandle(snap)  # 스냅샷 핸들 누수를 막는다.
    return None  # 전체 목록에 게임 이름이 없었다.


def _exe_path_of(pid: int) -> Optional[str]:
    """PID 의 실행 파일 경로. 읽기 전용 권한만 연다.

    PROCESS_QUERY_LIMITED_INFORMATION 하나면 충분하다. 권한을 더 들면
    1번 external_access 가 우리를 핵으로 잡는다(9/29 에 고친 그 문제다).
    """
    try:  # OS 조회 함수가 없다면 게임 경로를 모른다고 돌려준다.
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # 실행 경로 조회용 WinAPI를 불러온다.
        k32.OpenProcess.restype = wt.HANDLE  # 64비트 프로세스 핸들 반환형이다.
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]  # 권한·상속·PID 인자 형식이다.
        k32.CloseHandle.argtypes = [wt.HANDLE]  # 조회 후 핸들을 닫을 함수 형식이다.
        k32.QueryFullProcessImageNameW.argtypes = [  # 프로세스의 전체 exe 경로를 읽는 함수 형식이다.
            wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    except Exception:  # 권한이나 API 초기화 문제가 있어도 런처 자체는 살린다.
        return None  # 경로 미확인 상태로 상위 탐색 단계에 넘긴다.
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)  # 읽기 전용 수준의 핸들을 연다.
    if not h:  # 접근 거부 또는 프로세스 종료로 핸들을 못 열 수 있다.
        return None  # 실패를 임의 경로로 대체하지 않는다.
    try:  # 버퍼 조회가 성공·실패하더라도 열린 핸들을 닫는다.
        buf = ctypes.create_unicode_buffer(32768)  # 긴 Windows 경로까지 받을 문자 버퍼다.
        n = wt.DWORD(len(buf))  # 버퍼 길이를 전달하고 실제 문자열 길이를 받는다.
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):  # exe 절대경로 조회 성공 여부다.
            return buf.value  # 버퍼에 채워진 Unicode 경로를 반환한다.
    finally:  # 성공 시 return되더라도 반드시 실행된다.
        k32.CloseHandle(h)  # 프로세스 핸들 누수를 막는다.
    return None  # 실행 경로 조회에 실패했다.


def _steam_root() -> Optional[str]:
    """현재 사용자·시스템 레지스트리에서 실제 존재하는 Steam 루트를 찾는다."""
    try:  # Steam 설치 경로는 Windows 레지스트리에 기록된다.
        import winreg  # Windows에서만 제공되는 표준 라이브러리다.
    except ImportError:  # 비-Windows 테스트 환경에서는 레지스트리가 없다.
        return None  # Steam 경로를 알 수 없음을 명시한다.
    # 사용자별 Steam, 32비트 시스템 등록, 64비트 시스템 등록을 차례로 시도한다.
    for hive, key, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:  # 이 PC에 없는 키는 다음 후보로 넘어간다.
            with winreg.OpenKey(hive, key) as k:  # 읽은 레지스트리 핸들을 자동으로 닫는다.
                v = winreg.QueryValueEx(k, name)[0]  # Steam 설치 폴더 문자열만 꺼낸다.
        except OSError:  # 키 부재 또는 읽기 권한 문제다.
            continue  # 나머지 레지스트리 위치를 확인한다.
        if v and os.path.isdir(v):  # 저장된 값이 현재도 실제 폴더인지 확인한다.
            return v  # 가장 먼저 검증된 Steam 설치 루트를 쓴다.
    return None  # 어느 레지스트리 후보도 유효하지 않았다.


def _steam_libraries() -> list:
    """스팀 라이브러리 폴더 전부. 게임이 D 드라이브에 깔려 있어도 찾아야 한다."""
    libs, root = [], _steam_root()  # 기본 Steam 루트부터 찾는다.
    if root:  # Steam 자체가 설치된 경로를 첫 라이브러리로 취급한다.
        libs.append(root)  # 기본 `steamapps`도 게임을 담을 수 있다.
        try:  # 추가 드라이브의 라이브러리 목록을 읽는다.
            with open(os.path.join(root, "steamapps", "libraryfolders.vdf"),
                      encoding="utf-8", errors="replace") as f:  # 깨진 문자는 치환해 나머지를 읽는다.
                text = f.read()  # VDF 전체에서 라이브러리 `path` 항목을 찾는다.
            for m in re.finditer(r'"path"\s*"([^"]+)"', text):  # 모든 추가 라이브러리 경로다.
                libs.append(m.group(1).replace("\\\\", "\\"))  # VDF의 이스케이프된 역슬래시를 푼다.
        except OSError:  # 파일이 없거나 못 읽혀도 기본 Steam 루트는 사용한다.
            pass  # 기본 라이브러리만 계속 검사한다.
    out, seen = [], set()  # 반환 목록과 중복 방지용 정규화 키다.
    for p in libs:  # 기본·추가 라이브러리를 순서대로 검증한다.
        # vdf 는 'c:/program files (x86)/steam' 처럼 적어 두기도 한다. 글자만
        # 비교하면 레지스트리에서 얻은 같은 폴더를 두 번 훑게 된다.
        key = os.path.normcase(os.path.normpath(p))  # Windows 대소문자·구분자 차이를 없앤다.
        if os.path.isdir(p) and key not in seen:  # 실재하면서 아직 추가하지 않은 폴더만 쓴다.
            seen.add(key)  # 같은 라이브러리를 중복 검사하지 않는다.
            out.append(p)  # 원래 경로 표기는 실제 파일 열기에 사용한다.
    return out  # 찾은 순서를 보존한 검증된 라이브러리 목록이다.


def _root_in_library(lib: str) -> Optional[str]:
    """이 라이브러리 안의 게임 설치 폴더. 폴더 이름은 appmanifest 가 정답이다."""
    common = os.path.join(lib, "steamapps", "common")  # Steam 게임 폴더들의 공통 부모다.
    try:  # 게임별 appmanifest의 정확한 설치 폴더명을 우선 사용한다.
        with open(os.path.join(lib, "steamapps", f"appmanifest_{STEAM_APPID}.acf"),
                  encoding="utf-8", errors="replace") as f:  # 문자열 손상이 있어도 파일은 읽는다.
            m = re.search(r'"installdir"\s*"([^"]+)"', f.read())  # 설치 폴더명만 추출한다.
        if m:  # appmanifest에 installdir가 있을 때만 조립한다.
            p = os.path.join(common, m.group(1))  # Steam 라이브러리 아래 실제 후보 경로다.
            if os.path.isdir(p):  # 삭제된 게임의 오래된 manifest는 배제한다.
                return p  # 실제 존재하는 게임 설치 루트다.
    except OSError:  # appmanifest가 없거나 못 읽히면 폴더명으로 한 번 더 확인한다.
        pass  # 아래 기본 이름 후보로 진행한다.
    p = os.path.join(common, GAME_INSTALL_NAME)  # 이 게임의 통상적인 설치 폴더명이다.
    return p if os.path.isdir(p) else None  # 존재 여부를 확인한 뒤 결과를 돌린다.


_cache = {}  # 한 런처 실행 중에는 Steam·프로세스 탐색 결과를 재사용한다.


def _validated_game_root(candidate: str) -> Optional[str]:
    """사용자·Steam·프로세스에서 얻은 경로를 실제 게임 실행 파일로 검증한다.

    설치 루트, Win64 폴더, exe 경로 중 어느 것을 받아도 설치 루트로 돌린다.
    폴더가 있다는 이유만으로 다른 게임에 UE4SS를 설치하지 않기 위해 exe까지 본다.
    """
    if not candidate:  # 빈 환경변수·설정값은 경로 후보로 쓰지 않는다.
        return None  # 게임 위치를 아직 모른다는 의미다.
    path = os.path.abspath(os.path.expanduser(candidate.strip().strip('"')))  # 따옴표·상대경로·~를 정리한다.
    if os.path.basename(path).lower() == GAME_EXE.lower():  # 사용자가 exe 파일 자체를 선택했을 수 있다.
        path = os.path.dirname(path)  # 파일 경로를 게임 실행 폴더로 바꾼다.
    if os.path.basename(path).lower() == "win64":  # Win64 폴더를 넘겼다면 설치 루트까지 올라간다.
        root = _root_of_bin(path)  # Win64→Binaries→Chameleon→게임 루트의 세 단계다.
    else:  # exe/Win64가 아니면 전달받은 경로를 설치 루트 후보로 본다.
        root = path  # 나중에 아래 exe 존재 검사로 잘못된 폴더를 거른다.
    exe = os.path.join(root, WIN64_REL, GAME_EXE)  # 예상 설치 배치의 실제 게임 파일 경로다.
    return root if os.path.isfile(exe) else None  # 폴더뿐 아니라 게임 exe가 있어야 인정한다.


def save_game_root(candidate: str) -> str:
    """한 번 선택한 설치 위치를 다음 실행에도 쓰도록 저장한다."""
    root = _validated_game_root(candidate)  # 사용자가 선택한 exe/폴더를 설치 루트로 통일한다.
    if root is None:  # 실제 게임 파일이 없다면 잘못 선택한 것이다.
        raise ValueError(f"게임 실행 파일을 찾을 수 없습니다: {candidate}")  # 잘못된 설정을 저장하지 않는다.
    os.makedirs(os.path.dirname(SAVED_GAME_PATH), exist_ok=True)  # 사용자별 설정 폴더를 만든다.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                     dir=os.path.dirname(SAVED_GAME_PATH),
                                     prefix=".game-path-", delete=False) as f:  # 설정 저장 중 충돌하지 않을 임시 파일이다.
        json.dump({"game_root": root}, f, ensure_ascii=False, indent=2)  # 검증된 루트만 JSON으로 적는다.
        tmp = f.name  # 파일을 닫은 뒤 교체할 임시 경로를 기억한다.
    try:  # 기존 설정을 반쯤 써진 상태로 남기지 않기 위해 교체한다.
        os.replace(tmp, SAVED_GAME_PATH)  # 같은 볼륨 안에서 원자적으로 최신 설정을 적용한다.
    finally:  # 교체 실패 시 임시 파일을 남기지 않는다.
        if os.path.exists(tmp):  # 성공하면 이미 사라져 있으므로 확인한다.
            os.unlink(tmp)  # 실패 시 남은 임시 파일만 정리한다.
    _cache.clear()  # 직전에 못 찾았던 None 캐시를 버리고 새 선택을 반영한다.
    return root  # 앞으로 자식 모듈에 줄 설치 루트를 돌려준다.


def _saved_game_root() -> Optional[str]:
    """저장된 사용자 선택도 매번 실제 exe 존재 여부를 다시 확인한다."""
    try:  # 게임을 이동·삭제했거나 설정이 깨졌을 수 있다.
        with open(SAVED_GAME_PATH, encoding="utf-8") as f:  # 사용자별 설정 파일을 연다.
            value = json.load(f).get("game_root")  # 이전에 선택한 설치 루트만 읽는다.
    except (OSError, ValueError, AttributeError):  # 없음·잘못된 JSON·형식 오류를 묶는다.
        return None  # 저장값을 신뢰하지 않고 Steam 등의 다음 후보를 찾는다.
    return _validated_game_root(value) if isinstance(value, str) else None  # 실제 게임 exe가 여전히 있어야 쓴다.


def choose_game_root() -> Optional[str]:
    """Steam 탐색 실패 시 실행 파일을 한 번 선택받는다.

    콘솔 없는 패키지에서는 표준 파일 선택 창을 쓰고, 콘솔 실행에서는 경로 입력도
    가능하다. 취소하거나 잘못 선택한 경우엔 저장하지 않고 None을 돌린다.
    """
    picked = ""  # 취소나 GUI 실패 시의 기본값이다.
    try:  # 먼저 사용자가 파일을 눈으로 골라 실수할 가능성을 줄인다.
        from tkinter import Tk, filedialog  # Python 표준 GUI 파일 선택 창이다.
        root = Tk()  # 파일 선택 창의 부모 창을 잠시 만든다.
        root.withdraw()  # 불필요한 빈 Tk 창은 사용자에게 보이지 않게 한다.
        try:  # 대화상자가 닫힌 뒤 부모 창도 정리한다.
            picked = filedialog.askopenfilename(
                title="MECCHA CHAMELEON 게임 실행 파일 선택",  # 선택 목적을 명시한다.
                filetypes=[("게임 실행 파일", GAME_EXE), ("실행 파일", "*.exe")],  # 우선 게임 exe를 보여준다.
            )
        finally:  # 파일 선택을 취소해도 Tk 인스턴스를 남기지 않는다.
            root.destroy()  # GUI 리소스를 반환한다.
    except Exception:  # tkinter.TclError 등 GUI 초기화 실패면 콘솔 입력으로 대체
        if sys.stdin and sys.stdin.isatty():  # 사람이 직접 입력 가능한 콘솔일 때만 묻는다.
            try:  # 입력이 취소되면 런처가 계속 실패 이유를 표시하게 한다.
                picked = input(f"게임 설치 폴더 또는 {GAME_EXE} 경로: ").strip()  # 복사한 경로도 받는다.
            except (EOFError, KeyboardInterrupt):  # 사용자가 취소하거나 입력 스트림이 닫혔다.
                return None  # 설치 위치를 임의로 추측하지 않는다.
    if not picked:  # 대화상자를 취소하거나 아무 경로도 입력하지 않았다.
        return None  # 저장하지 않고 호출자에게 선택 실패를 알린다.
    try:  # 선택한 경로의 게임 exe 존재를 확인한 뒤 저장한다.
        return save_game_root(picked)  # 성공하면 검증된 설치 루트를 돌려준다.
    except (OSError, ValueError):  # 저장 권한 또는 잘못된 exe 선택 문제다.
        return None  # 호출자가 게임 경로를 못 찾았다고 표시한다.


def find_game_root() -> Optional[str]:
    """게임 설치 폴더(...\\MECCHA CHAMELEON). 못 찾으면 None.

    예전에는 modules.GAME_DIR 한 줄에 박힌 경로를 그냥 썼다. 그 경로가 없는
    PC 에서는 steam://rungameid 로 넘어가긴 하지만 **폴더를 아는 것 자체가
    안 됐다.** 다른 PC 에서 정상 세션을 찍기로 한 이상 이대로는 막힌다.

    찾는 순서는 확실한 것부터다.
        1. GZZ_GAME_DIR 환경변수      사람이 직접 지정한 것
        2. 떠 있는 게임 프로세스       추측이 아니라 사실
        3. 사용자가 저장한 위치         한 번 선택한 설치 폴더
        4. 스팀 라이브러리             레지스트리 + libraryfolders.vdf
        5. modules.GAME_DIR           옛 기본값이 실제로 맞는 PC만
        6. 파일 선택 창에서 한 번 선택해 저장 (콘솔 없는 EXE도 포함)
    """
    if "root" in _cache:  # 이번 런처 실행에서 이미 탐색했는지 확인한다.
        return _cache["root"]  # 반복 레지스트리·프로세스 조회를 피한다.
    got = None  # 각 경로 후보가 성공할 때만 설치 루트가 채워진다.
    env = (os.environ.get("GZZ_GAME_DIR") or "").strip()  # 명시적 운영자 지정이 최우선이다.
    if env:  # 빈 설정값은 무시한다.
        got = _validated_game_root(env)  # 환경변수도 exe 존재를 확인해야 쓴다.
    if got is None:  # 명시적 경로가 없거나 틀렸다면 실제 게임 프로세스를 본다.
        pid = find_game_pid()  # 현재 떠 있는 게임 PID를 찾는다.
        if pid is not None:  # 게임이 실행 중일 때는 실제 exe 경로가 가장 확실하다.
            exe = _exe_path_of(pid)  # 최소 조회 권한으로 exe 절대경로를 읽는다.
            if exe:  # 권한 문제로 경로를 못 읽었다면 다른 후보로 넘어간다.
                got = _validated_game_root(exe)  # exe 경로에서 설치 루트를 역산해 검증한다.
    if got is None:  # 게임이 안 떠 있으면 이전에 사용자가 선택한 경로를 본다.
        got = _saved_game_root()  # 이동·삭제된 게임을 제외하기 위해 다시 검증한다.
    if got is None:  # 사용자 선택도 없다면 Steam 라이브러리를 훑는다.
        for lib in _steam_libraries():  # Steam 기본 폴더와 추가 드라이브 전부다.
            got = _validated_game_root(_root_in_library(lib) or "")  # manifest의 게임 폴더를 exe로 확인한다.
            if got:  # 첫 유효한 설치본을 찾았다.
                break  # 뒤의 라이브러리는 더 볼 필요가 없다.
    if got is None:  # 구버전 고정 경로가 실제로 맞는 PC도 지원한다.
        got = _validated_game_root(GAME_DIR)  # 하드코딩 경로도 검증 없이 사용하지 않는다.
    if got is None:  # 콘솔 없는 EXE에서도 파일 선택 창은 열 수 있어야 한다.
        got = choose_game_root()  # 사용자가 한 번 선택한 값을 다음 실행용으로 저장한다.
    _cache["root"] = got  # 실패(None)까지 캐시해 중복 파일 선택 창을 막는다.
    return got  # 설치 루트 또는 미발견(None)을 반환한다.


def _root_of_bin(bin_dir: str) -> str:
    """...\\Chameleon\\Binaries\\Win64 -> ...\\MECCHA CHAMELEON (세 단계 위)."""
    p = bin_dir  # 입력은 게임 exe가 있는 Win64 폴더다.
    for _ in range(3):  # Win64·Binaries·Chameleon 각 디렉터리를 벗어난다.
        p = os.path.dirname(p)  # 한 단계 위 부모 폴더로 올라간다.
    return p or bin_dir  # 드라이브 루트 같은 예외에도 빈 문자열은 돌려주지 않는다.


def find_game_dir() -> str:
    """실행 파일이 있는 폴더. 못 찾으면 예전 기본값을 그대로 돌려준다.

    None 을 돌려주지 않는 이유는 호출부가 전부 경로를 기대하고 있어서다.
    찾았는지 여부는 `find_game_root() is not None` 으로 보면 된다.
    """
    if "bin" in _cache:  # 이미 계산된 실행 폴더가 있다.
        return _cache["bin"]  # 모듈별 호출마다 같은 경로를 쓴다.
    root = find_game_root()  # 환경변수·프로세스·Steam 등을 통해 설치 루트를 얻는다.
    got = os.path.join(root, WIN64_REL) if root else GAME_DIR  # 없으면 기존 호출부용 기본 문자열을 준다.
    _cache["bin"] = got  # 자식 모듈에 넘길 경로를 일관되게 유지한다.
    return got  # exe·UE4SS가 놓일 Win64 경로다.


def launch() -> bool:
    """Steam이 관리하는 게임은 Steam을 통해 띄우고, URI 실패 때만 EXE를 시도한다.

    반환값은 실행 *요청* 성공 여부다. 실제 게임 시작은 wait_for_game()에서 확인한다.
    """
    if find_game_pid() is not None:  # 사용자가 게임을 먼저 실행했을 수 있다.
        return False  # 두 번째 게임 창을 만들지 않았음을 반환한다.
    try:  # Steam 게임을 EXE만 직접 켜면 창 없이 프로세스만 남을 수 있다.
        os.startfile(f"steam://rungameid/{STEAM_APPID}")  # Steam이 인증·실행 인자를 처리하게 한다.
        return True  # Steam에 실행을 요청했다. 창까지 떴다는 뜻은 아니다.
    except OSError:  # URI 핸들러가 없을 때만 기존 직접 실행 경로를 시도한다.
        pass
    game_dir = find_game_dir()  # URI 실패 때 사용할 검증된 게임 실행 폴더다.
    exe = os.path.join(game_dir, GAME_EXE)  # 직접 실행할 파일 경로다.
    if not os.path.isfile(exe):  # Steam도 직접 실행도 불가능한 상태다.
        return False
    try:
        subprocess.Popen([exe], cwd=game_dir)  # 마지막 대안에서도 UE4SS 상대경로 기준을 유지한다.
        return True  # 프로세스 생성 요청만 성공했다. 실제 창은 별도로 확인해야 한다.
    except OSError:
        return False


def wait_for_game(timeout_s: float = 120.0, poll_s: float = 1.0) -> Optional[int]:
    """게임이 뜰 때까지 기다린다. 떴으면 PID, 시간이 다 되면 None."""
    end = time.monotonic() + timeout_s  # 시스템 시간이 바뀌어도 대기 제한이 흔들리지 않는다.
    while time.monotonic() < end:  # 지정한 대기 시간 안에서만 검사한다.
        pid = find_game_pid()  # 게임 프로세스 목록을 다시 조회한다.
        if pid is not None:  # 실행 요청 뒤 게임이 실제로 떴다.
            return pid  # 런처가 이후 모듈에 사용할 PID다.
        time.sleep(poll_s)  # 바쁜 반복으로 CPU를 소모하지 않게 쉬었다가 본다.
    return None  # 시간 안에 게임을 확인하지 못했다.


@dataclass(frozen=True)
class UE4SSResult:
    """READY만 설치 파일과 모드 활성화가 준비됐다는 뜻이다.

    게임 안에서 로드됐다는 뜻은 아니다. 그 확인은 verify_ue4ss_log()가 한다.
    """
    status: str  # READY/MISSING/CONFLICT/GAME_RUNNING/ERROR 등의 기계 판별 값이다.
    detail: str  # 런처 화면에 바로 보여줄 구체적 이유다.
    installed: int = 0  # 이번 호출에서 새로 복사한 파일 개수다.


def _sha256(path: Path) -> str:
    """큰 ZIP·DLL도 한 번에 메모리에 올리지 않고 해시를 계산한다."""
    h = hashlib.sha256()  # 새 SHA-256 계산기를 만든다.
    with path.open("rb") as stream:  # 바이트 그대로 읽어 텍스트 인코딩 영향을 없앤다.
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):  # 1 MiB씩 EOF까지 반복한다.
            h.update(chunk)  # 각 조각을 같은 해시 계산에 누적한다.
    return h.hexdigest()  # 비교·등록부에 쓸 64자리 소문자 문자열이다.


def _archive_entry(archive: zipfile.ZipFile, candidates: tuple,
                   *, required: bool = True) -> Optional[bytes]:
    """필요한 멤버만 읽는다. ZIP 경로를 그대로 추출하지 않아 경로 이탈이 없다."""
    matches = []  # 이름이 맞는 ZIP 항목을 모두 모아 중복 여부도 검사한다.
    for item in archive.infolist():  # 실제 압축 파일 목록만 보고 경로를 직접 추출하지 않는다.
        name = item.filename.replace("\\", "/").strip("/").lower()  # ZIP 내 경로 표기를 통일한다.
        if item.is_dir():  # 폴더 자체는 대상 파일이 아니다.
            continue  # 다음 ZIP 항목으로 넘어간다.
        if any(name == c.lower() or name.endswith("/" + c.lower()) for c in candidates):  # 허용한 상대경로만 찾는다.
            matches.append(item)  # 한 단계 상위 폴더로 감싼 배포 ZIP도 인식한다.
    if not matches and not required:  # mods.txt처럼 없어도 생성 가능한 항목이다.
        return None  # 호출자가 기본값을 채우게 한다.
    if len(matches) != 1:  # 누락·중복은 어느 버전을 설치할지 모호하다.
        raise ValueError(f"묶음에서 {candidates[0]} 항목이 하나여야 합니다 (현재 {len(matches)}개)")  # 복사 전에 중단한다.
    item = matches[0]  # 유일하게 선택된 파일 항목이다.
    mode = item.external_attr >> 16  # ZIP의 Unix 파일 종류 메타데이터를 꺼낸다.
    if stat.S_IFMT(mode) == stat.S_IFLNK or item.file_size > MAX_BUNDLE_MEMBER:  # 링크·과대 항목을 막는다.
        raise ValueError(f"허용하지 않는 묶음 항목: {item.filename}")  # 예상 밖의 항목은 설치하지 않는다.
    return archive.read(item)  # 고정 SHA로 검증된 ZIP에서 해당 파일 바이트만 읽는다.


def _directory_payload(source: Path) -> tuple:
    """폴더 배포본의 필요한 파일만 읽고 경로·바이트 기반 지문을 계산한다.

    ZIP 전체 해시와 달리 폴더에는 단일 파일 SHA가 없다. 설치할 네 파일의
    상대경로와 내용을 정해진 순서로 해시해 폴더 이름·수정 시각과 무관하게 고정한다.
    """
    if source.is_symlink() or (hasattr(source, "is_junction") and source.is_junction()):  # 바깥 폴더로 우회하지 않는다.
        raise ValueError("링크/정션 배포 폴더는 사용할 수 없습니다")  # 신뢰할 경로를 하나로 고정한다.
    payload = {}  # 실제 게임 폴더에 설치할 상대경로별 바이트다.
    for target, candidates in UE4SS_BUNDLE_FILES.items():  # 런타임 필수 파일만 고른다.
        matches = [source / Path(candidate) for candidate in candidates
                   if (source / Path(candidate)).is_file()]  # 허용한 배치만 인정한다.
        if len(matches) != 1:  # 빠졌거나 두 배치에 모두 있으면 설치 원본이 모호하다.
            raise ValueError(f"폴더에서 {target} 항목이 하나여야 합니다 (현재 {len(matches)}개)")  # 쓰기 전에 실패한다.
        item = matches[0]  # 실제로 찾은 하나의 원본 파일이다.
        if _linked_destination(source, item):  # 원본 폴더 내부 링크도 외부 파일을 읽게 할 수 있다.
            raise ValueError(f"링크/정션 배포 파일은 사용할 수 없습니다: {item}")  # 경로 우회를 거부한다.
        if item.stat().st_size > MAX_BUNDLE_MEMBER:  # 실수로 과대한 파일을 설치하지 않는다.
            raise ValueError(f"허용하지 않는 배포 파일 크기: {item}")  # 메모리 과사용도 막는다.
        payload[target] = item.read_bytes()  # 검증 지문과 실제 설치에 같은 바이트를 사용한다.
    fingerprint = hashlib.sha256()  # 폴더 배포본을 식별할 고정 길이 지문이다.
    for target in sorted(payload):  # OS 디렉터리 나열 순서와 무관하게 계산한다.
        data = payload[target]  # 해당 설치 대상의 실제 바이트다.
        fingerprint.update(target.encode("utf-8") + b"\0")  # 경로도 지문에 포함한다.
        fingerprint.update(len(data).to_bytes(8, "big") + data)  # 파일 길이·내용을 함께 묶는다.
    return payload, fingerprint.hexdigest()  # 이후 고정 해시와 비교하고 그대로 설치한다.


def _desired_mods_text(existing: bytes) -> bytes:
    """다른 모드·주석·줄바꿈은 보존하고 팀 관측 모드만 활성화한다."""
    bom = existing.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM이 있었다면 다시 붙인다.
    text = existing.decode("utf-8-sig")  # BOM을 제거하고 줄별 수정을 위해 문자열로 읽는다.
    newline = "\r\n" if "\r\n" in text else "\n"  # 원래 파일의 줄바꿈 관례를 유지한다.
    mod_names = "|".join(re.escape(name) for name in TEAM_MODS)  # 팀 모드 명단을 한 곳에서 관리한다.
    seen = set()  # 팀 모드가 이미 한 번 등장했는지 기억한다.
    out = []  # 보존·수정한 줄을 원래 순서대로 쌓는다.
    for line in text.splitlines(keepends=True):  # 다른 모드·주석의 줄 끝까지 보존한다.
        body = line.rstrip("\r\n")  # 모드 이름과 숫자를 해석할 부분이다.
        ending = line[len(body):]  # 해당 줄의 기존 CRLF/LF를 따로 보관한다.
        match = re.fullmatch(rf"(\s*)({mod_names})(\s*:\s*)[01](\s*(?:[#;].*)?)",
                             body, flags=re.IGNORECASE)  # 팀 모드의 0/1 설정만 안전하게 인정한다.
        if match:  # 이 줄은 우리가 활성화해야 할 모드다.
            name = next(n for n in TEAM_MODS if n.lower() == match.group(2).lower())  # 정식 이름으로 통일한다.
            if name in seen:  # 같은 모드가 중복 선언돼 있으면 두 번째부터 버린다.
                continue  # 다른 줄은 그대로 계속 처리한다.
            seen.add(name)  # 이후 중복 줄을 알아볼 표시다.
            out.append(f"{match.group(1)}{name}{match.group(3)}1{match.group(4)}{ending}")  # 숫자만 1로 바꾼다.
        elif re.match(rf"\s*(?:{mod_names})\s*:", body, re.IGNORECASE):  # 팀 모드인데 0/1이 아니다.
            raise ValueError(f"해석할 수 없는 모드 설정: {body}")  # 임의로 덮어써 사용자의 설정을 훼손하지 않는다.
        else:  # 다른 모드나 주석이다.
            out.append(line)  # 해당 줄은 바이트 의미를 바꾸지 않고 둔다.
    if out and not out[-1].endswith(("\n", "\r")):  # 마지막 기존 줄에 개행이 없을 수 있다.
        out.append(newline)  # 새 모드 줄이 앞줄에 붙지 않도록 분리한다.
    for name in TEAM_MODS:  # 각 팀 모드가 한 줄씩만 있는지 확인한다.
        if name not in seen:  # 원래 mods.txt에 없던 팀 모드다.
            out.append(f"{name} : 1{newline}")  # 다른 설정은 건드리지 않고 끝에 추가한다.
    return (b"\xef\xbb\xbf" if bom else b"") + "".join(out).encode("utf-8")  # 원래 인코딩 표식을 복원한다.


def _linked_destination(bin_path: Path, destination: Path) -> bool:
    """게임 폴더 안의 링크/정션을 경유해 엉뚱한 위치에 쓰지 않는다."""
    path = destination  # 설치될 실제 파일 경로부터 검사한다.
    while path != bin_path:  # 게임의 Win64 기준 폴더에 도달할 때까지 부모를 확인한다.
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):  # 우회 경로다.
            return True  # 링크를 따라 외부 파일을 쓰는 일을 막는다.
        path = path.parent  # 한 단계 위 디렉터리도 검사한다.
    return False  # 검사 범위 안에 링크/정션이 없다.


def prepare_ue4ss(game_root: str, bundle_zip: Optional[str] = None,
                  expected_sha256: Optional[str] = None,
                  *, signature_path: Optional[str] = None,
                  expected_signature_sha256: Optional[str] = None,
                  game_running: Optional[bool] = None) -> UE4SSResult:
    """고정한 ZIP 또는 폴더와 팀 Lua 모드를 검사·설치하고 해시 등록부를 기록한다.

    다른 버전은 덮어쓰지 않는다. 한 파일이라도 충돌하면 쓰기 전에 중단한다.
    별도 StaticConstructObject.lua는 기본 배포본에 없으므로 요청한 경우에만 설치한다.
    """
    root = _validated_game_root(game_root)  # 설치 위치가 정말 이 게임의 폴더인지 확인한다.
    if root is None:  # 잘못된 경로라면 다른 게임 폴더를 수정하면 안 된다.
        return UE4SSResult("ERROR", "게임 실행 파일이 있는 설치 폴더를 먼저 선택하세요")  # 쓰기 전 실패다.
    bundle_value = bundle_zip or os.environ.get(UE4SS_BUNDLE_ENV, "")  # 명시 인자·환경변수 순으로 배포본 위치를 고른다.
    if not bundle_value:  # Path("")가 현재 디렉터리를 가리키는 일을 막는다.
        return UE4SSResult("MISSING", "팀 UE4SS 배포본 경로가 없습니다")  # 설치 원본 미지정이다.
    bundle_path = Path(bundle_value)  # ZIP 파일과 압축을 푼 폴더를 모두 허용한다.
    pinned = (PINNED_UE4SS_DIRECTORY_SHA256 if bundle_path.is_dir()
              else PINNED_UE4SS_ZIP_SHA256)  # 서로 다른 지문 방식을 섞지 않는다.
    expected = expected_sha256 if expected_sha256 is not None else pinned  # 팀이 확정한 해시다.
    if not expected or not re.fullmatch(r"[a-fA-F0-9]{64}", expected):  # 미확정·오타 해시는 신뢰할 수 없다.
        return UE4SSResult("MISSING", "팀 UE4SS 배포본의 고정 SHA-256이 아직 없습니다")  # 기본 실행은 설치 금지다.
    if not bundle_path.is_file() and not bundle_path.is_dir():  # 해시만 있어도 실물이 없으면 진행할 수 없다.
        return UE4SSResult("MISSING", f"팀 UE4SS 배포본이 없습니다: {bundle_path}")  # 누락 경로를 화면에 남긴다.
    signature_value = signature_path if signature_path is not None else os.environ.get(SIGNATURE_ENV, "")  # 별도 파일은 명시된 경우에만 쓴다.
    signature_expected = (expected_signature_sha256 if expected_signature_sha256 is not None
                          else PINNED_SIGNATURE_SHA256)  # 인자가 없으면 코드에 고정된 팀 해시를 쓴다.
    if signature_value and not re.fullmatch(r"[a-fA-F0-9]{64}", signature_expected):  # 명시한 파일은 해시 없이 설치하지 않는다.
        return UE4SSResult("MISSING", "지정한 StaticConstructObject.lua의 고정 SHA-256이 없습니다")  # 무검증 별도 파일은 거부한다.
    if not signature_value and signature_expected:  # 해시만 있고 파일 위치가 없는 설정 오류다.
        return UE4SSResult("MISSING", "고정 SHA-256에 해당하는 StaticConstructObject.lua 경로가 없습니다")
    signature = Path(signature_value) if signature_value else None  # 기본 배포에서는 별도 파일을 사용하지 않는다.
    if signature is not None and not signature.is_file():  # 요청한 파일만 존재를 확인한다.
        return UE4SSResult("MISSING", f"지정한 시그니처가 없습니다: {signature}")
    try:  # 아래 검사 중 하나라도 실패하면 설치 상태를 원인별로 돌려준다.
        if bundle_path.is_dir():  # 압축을 푼 폴더 배포본이다.
            payload, actual = _directory_payload(bundle_path)  # 설치할 파일 바이트와 묶음 지문을 한 번에 얻는다.
            bundle_kind = "directory-payload"  # manifest에서도 ZIP 해시와 구분한다.
        else:  # 원본 ZIP 배포본이다.
            actual = _sha256(bundle_path)  # ZIP 전체 바이트의 SHA-256을 계산한다.
            bundle_kind = "zip"  # 전체 아카이브 해시임을 기록한다.
            payload = {}  # ZIP 검증을 통과하면 필요한 파일만 이곳에 채운다.
        if actual.lower() != expected.lower():  # 팀이 정한 정확한 배포본과 비교한다.
            return UE4SSResult("CONFLICT", "UE4SS 배포본 SHA-256이 팀 고정값과 다릅니다")  # 다른 빌드는 설치하지 않는다.
        if signature is not None and _sha256(signature).lower() != signature_expected.lower():  # 요청한 파일만 독립 검증한다.
            return UE4SSResult("CONFLICT", "게임 전용 시그니처 SHA-256이 팀 고정값과 다릅니다")  # 바뀐 파일은 거부한다.
        if bundle_kind == "zip":  # ZIP은 파일 해시 확인 뒤에만 내부 항목을 읽는다.
            with zipfile.ZipFile(bundle_path) as archive:  # ZIP을 읽기 전용으로 연다.
                for target, candidates in UE4SS_BUNDLE_FILES.items():  # 꼭 필요한 런타임 항목만 추린다.
                    payload[target] = _archive_entry(archive, candidates)  # 대상 경로는 코드가 고정한다.
        # 두 형식 모두 기본 mods.txt는 CheatManager 같은 모드를 켤 수 있어 복사하지 않는다.
        source = Path(__file__).resolve().parents[1]  # 두 소스 위치를 모두 포함하는 client 폴더다.
        if signature is not None:  # 기본 ZIP에 없는 게임별 대체 시그니처는 요청했을 때만 설치한다.
            payload["ue4ss/UE4SS_Signatures/StaticConstructObject.lua"] = signature.read_bytes()
        for target, relative in TEAM_MOD_FILES.items():  # 팀이 작성한 Lua 파일만 추가한다.
            payload[target] = (source / relative).read_bytes()  # 레포 파일 내용을 그대로 설치한다.
        bin_path = Path(root) / WIN64_REL  # UE4SS가 게임과 함께 읽힐 Win64 폴더다.
        existing_signature = bin_path / "ue4ss" / "UE4SS_Signatures" / "StaticConstructObject.lua"
        if signature is None and (existing_signature.exists() or existing_signature.is_symlink()
                                  or _linked_destination(bin_path, existing_signature)):
            return UE4SSResult("CONFLICT", f"검증되지 않은 기존 시그니처가 있습니다: {existing_signature}")
        if (bin_path / "dwmapi.dll.off").exists():  # 사용자가 기존 프록시를 꺼 둔 흔적이다.
            return UE4SSResult("CONFLICT", "기존 dwmapi.dll.off가 있습니다. 먼저 설치 상태를 확인하세요")  # 자동 덮어쓰기 금지다.
        paths = {bin_path / Path(relative): data for relative, data in payload.items()}  # 모든 대상 절대경로다.
        mods_path = bin_path / "ue4ss" / "Mods" / "mods.txt"  # 다른 모드도 적혀 있는 설정 파일이다.
        for path, data in paths.items():  # 실제 쓰기 전에 모든 파일의 충돌을 검사한다.
            if _linked_destination(bin_path, path):  # 링크/정션을 거치면 다른 위치에 쓰게 된다.
                return UE4SSResult("CONFLICT", f"링크/정션 경로에는 설치하지 않습니다: {path}")  # 경로 우회를 막는다.
            if path.exists() and (not path.is_file() or _sha256(path) != hashlib.sha256(data).hexdigest()):  # 바이트 비교다.
                return UE4SSResult("CONFLICT", f"다른 버전이 이미 있습니다: {path}")  # 하나라도 다르면 아무것도 덮지 않는다.
        if _linked_destination(bin_path, mods_path):  # mods.txt도 같은 경로 안전 검사를 받는다.
            return UE4SSResult("CONFLICT", f"링크/정션 경로에는 설치하지 않습니다: {mods_path}")  # 우회 설정을 막는다.
        if mods_path.exists() and not mods_path.is_file():  # 파일 대신 폴더 등이 있으면 수정할 수 없다.
            return UE4SSResult("CONFLICT", f"모드 설정이 파일이 아닙니다: {mods_path}")  # 비정상 배치를 명확히 표시한다.
        previous_mods = mods_path.read_bytes() if mods_path.exists() else b""  # 새 설치는 팀 모드만 활성화한다.
        desired_mods = _desired_mods_text(previous_mods)  # 다른 모드를 보존하며 네 관측 모드만 켠다.
        changes = sum(not p.exists() for p in paths) + (previous_mods != desired_mods or not mods_path.exists())  # 변경 필요 수다.
        if game_running is None:  # 테스트가 명시하지 않았다면 실제 게임 생존 여부를 본다.
            game_running = find_game_pid() is not None  # 실행 중인 게임 파일을 수정하지 않기 위한 검사다.
        if game_running and changes:  # 설치·설정 변경은 게임 종료 뒤에만 허용한다.
            return UE4SSResult("GAME_RUNNING", "게임을 종료한 뒤 UE4SS를 설치·수정하세요")  # 무중단 적용을 가장하지 않는다.

        installed = 0  # 실제 새로 복사한 파일만 센다.
        for path, data in paths.items():  # 사전 충돌 검사를 통과한 파일만 설치한다.
            if path.exists():  # 해시가 같다고 사전 확인한 기존 파일이다.
                continue  # 불필요한 복사를 반복하지 않는다.
            path.parent.mkdir(parents=True, exist_ok=True)  # 해당 파일의 상위 폴더만 만든다.
            with path.open("xb") as f:  # 설치 도중 다른 프로그램이 만든 파일은 덮지 않는다.
                f.write(data)  # 검증된 바이트를 그대로 기록한다.
            installed += 1  # 이번 호출이 만든 파일 수를 갱신한다.
        if previous_mods != desired_mods or not mods_path.exists():  # 팀 모드 설정이 달라졌을 때만 쓴다.
            mods_path.parent.mkdir(parents=True, exist_ok=True)  # UE4SS Mods 폴더를 준비한다.
            with tempfile.NamedTemporaryFile(dir=mods_path.parent, prefix=".mods-", delete=False) as tmp:  # 임시 파일이다.
                tmp.write(desired_mods)  # 원본을 바로 잘라내지 않고 새 설정을 먼저 완성한다.
                temp_name = tmp.name  # 닫힌 다음 교체할 파일 경로를 보관한다.
            try:  # 다른 프로세스가 동시에 설정을 바꿨다면 덮지 않는다.
                if mods_path.exists() and mods_path.read_bytes() != previous_mods:  # 사전 확인값과 다시 비교한다.
                    return UE4SSResult("CONFLICT", "설치 도중 mods.txt가 바뀌었습니다", installed)  # 경합을 알린다.
                os.replace(temp_name, mods_path)  # 완성된 새 설정으로 한 번에 교체한다.
            finally:  # 교체 성공·실패와 관계없이 남은 임시 파일을 정리한다.
                if os.path.exists(temp_name):  # 성공하면 이미 옮겨졌으므로 남아 있지 않다.
                    os.unlink(temp_name)  # 실패한 경우에만 임시 파일을 지운다.
        import ue4ss_manifest  # 설치한 파일의 SHA-256을 파일시스템 탐지기와 공유한다.
        recorded = ue4ss_manifest.record(
            root, [str(p) for p in paths],  # 게임 루트와 이번에 검증·설치한 정확한 파일 경로 목록이다.
            bundle={"name": "UE4SS", "version": UE4SS_VERSION,
                    "sha256": actual, "kind": bundle_kind},  # ZIP 전체 해시인지 폴더 설치 파일 지문인지 구분한다.
            mods=list(TEAM_MODS))  # 우리 모드에 속한 파일만 탐지기 예외 후보가 된다.
        if recorded.get("unreadable"):  # 어떤 파일의 해시도 누락시키면 안 된다.
            return UE4SSResult("ERROR", "설치 파일 해시를 기록하지 못했습니다", installed)  # 정상 설치로 표시하지 않는다.
        return UE4SSResult("READY", "파일·모드 준비 완료; 게임 실행 뒤 로드 로그 확인 필요", installed)  # 로드 검증은 별도다.
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:  # 파일·인코딩·ZIP 오류다.
        return UE4SSResult("ERROR", f"UE4SS 준비 실패: {exc}")  # 원인을 UI에 보여주고 정상으로 위장하지 않는다.


def verify_ue4ss_log(game_root: str, session_id: Optional[str], started_after: float) -> UE4SSResult:
    """새 게임 실행의 로그에서 본체·모드 로드를 확인한다.

    세션 ID를 주면 페인트 옵저버의 세션 연결까지 확인하고, 없으면 로드만 확인한다.
    """
    root = _validated_game_root(game_root)  # 로그도 앞서 검증한 게임 설치 폴더에서만 찾는다.
    if root is None:  # 게임 폴더가 바뀌거나 삭제됐을 수 있다.
        return UE4SSResult("ERROR", "게임 폴더를 확인할 수 없습니다")  # 다른 설치본 로그를 오인하지 않는다.
    log = Path(root) / WIN64_REL / "ue4ss" / "UE4SS.log"  # 본체 DLL과 같은 폴더의 로그다.
    try:  # 로그가 없거나 접근 불가면 로드 확인 실패로 남긴다.
        if log.stat().st_mtime < started_after:  # 대기 시작 전에 남은 로그는 성공으로 재사용하지 않는다.
            return UE4SSResult("STALE", "이번 게임 실행의 UE4SS.log가 아닙니다")  # 이전 세션 성공을 재사용하지 않는다.
        text = log.read_text(encoding="utf-8", errors="replace")  # 확인할 메시지 전체를 읽는다.
    except OSError:  # UE4SS가 로드되지 않았다면 로그 파일이 없을 수 있다.
        return UE4SSResult("MISSING", "UE4SS.log가 생성되지 않았습니다")  # 파일 존재를 성공으로 추측하지 않는다.
    # 본체 버전·초기화·네 모드 로드를 각각 확인한다.
    checks = {
        "빌드 SHA": "f6d5f942" in text.lower(),  # 같은 버전명의 다른 빌드를 배제한다.
        "PS scan": "ps scan successful" in text.lower(),  # UE4SS 내부 스캔 완료 표시다.
        "DamageLogger 로드": "[DamageLogger] loaded" in text,  # 에임봇 관측 모드의 시작 표시다.
        "GZZPaintObserver 로드": "[GZZPaintObserver] loaded" in text,  # 페인트 관측 모드 시작 표시다.
        "NoclipLogger 로드": "[NoclipLogger] loaded" in text,  # 노클립 관측 모드 시작 표시다.
        "GodModeTelemetry 로드": "[GodModeTelemetry] Telemetry sensor loaded" in text,  # 갓모드 관측 모드 시작 표시다.
    }
    if session_id:  # 탐지기가 session.control을 만든 실전 세션이라면 연결도 확인한다.
        checks["현재 세션"] = f"[GZZPaintObserver] session={session_id}" in text
    failed = [name for name, good in checks.items() if not good]  # 누락된 근거만 모아 오류 설명을 만든다.
    if failed:  # 일부만 로드됐다면 정상 행동 탐지로 표시하면 안 된다.
        return UE4SSResult("UNAVAILABLE", "행동 탐지 불가: " + ", ".join(failed) + " 기록 없음")  # 빠진 신호를 알린다.
    detail = "이번 게임 실행에서 UE4SS·팀 모드 4개 로드 확인"
    if not session_id:  # 모드 로드는 확인했지만 탐지기의 세션 연결까지 주장하지 않는다.
        detail += " (세션 연결은 별도 확인 필요)"
    return UE4SSResult("READY", detail)


def wait_for_ue4ss_log(game_root: str, session_id: Optional[str], started_after: float,
                       timeout_s: float = 20.0, poll_s: float = 0.5) -> UE4SSResult:
    """게임과 옵저버가 로그를 쓰는 동안 기다린다. 시간 초과를 정상으로 취급하지 않는다."""
    deadline = time.monotonic() + max(0.0, timeout_s)  # 시스템 시각 변경과 무관한 종료 시각이다.
    while True:  # 로그가 늦게 생성·추가되는 경우를 잠시 기다린다.
        result = verify_ue4ss_log(game_root, session_id, started_after)  # 매번 최신 로그를 다시 검사한다.
        if result.status == "READY" or time.monotonic() >= deadline:  # 성공 또는 대기 종료다.
            return result  # 시간 초과 시에도 MISSING/UNAVAILABLE을 그대로 반환한다.
        time.sleep(max(0.05, poll_s))  # 너무 짧은 폴링 간격은 CPU·디스크를 낭비한다.


def _cli(argv=None) -> int:
    """팀 런처 본체를 바꾸지 않고 설치와 실제 게임 로드를 한 단계씩 시험한다."""
    parser = argparse.ArgumentParser(description="MECCHA CHAMELEON UE4SS 설치·로드 점검")
    parser.add_argument("action", choices=("prepare", "wait-load"), help="파일 설치 또는 새 게임 로그 대기")
    parser.add_argument("--game-dir", help="게임 설치 루트, Win64 폴더 또는 게임 EXE 경로")
    parser.add_argument("--bundle", help="팀이 검증한 UE4SS ZIP 또는 폴더; prepare에 필요")
    parser.add_argument("--session-id", help="wait-load에서 페인트 옵저버의 세션 연결까지 확인할 때만 지정")
    parser.add_argument("--timeout", type=float, default=180.0, help="wait-load 대기 시간(초, 기본 180)")
    args = parser.parse_args(argv)
    root = _validated_game_root(args.game_dir) if args.game_dir else find_game_root()
    if not root:
        print("ERROR: PenguinHotel-Win64-Shipping.exe가 있는 게임 폴더를 찾지 못했습니다")
        return 2
    if args.action == "prepare":
        result = prepare_ue4ss(root, bundle_zip=args.bundle, signature_path="")  # 직접 점검은 받은 ZIP만 사용한다.
    else:
        started_after = time.time()
        print("지금 게임을 실행하세요. 이번 실행의 UE4SS.log를 기다립니다.", flush=True)
        result = wait_for_ue4ss_log(root, args.session_id, started_after, timeout_s=args.timeout)
    print(f"{result.status}: {result.detail} (새로 설치한 파일 {result.installed}개)")
    return 0 if result.status == "READY" else 1


if __name__ == "__main__":
    sys.exit(_cli())
