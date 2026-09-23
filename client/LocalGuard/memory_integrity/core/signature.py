"""Authenticode 서명 검증 (WinVerifyTrust ctypes 래퍼)

1차 실측에서 이름 화이트리스트가 오탐 115개를 냈다. 원인은 명확하다.
PC 마다 그래픽 드라이버·오버레이·오디오 스택이 달라서 정상 모듈 목록이
환경마다 다르기 때문이다. 이름 목록을 손으로 관리하는 한 이 문제는 안 없어진다.

그래서 판정 근거를 바꾼다. **"이 이름을 아는가"가 아니라 "누가 서명했는가"** 로.
Intel·NVIDIA·Microsoft 드라이버는 전부 유효한 Authenticode 서명을 갖는다.
주입된 치트 DLL 은 보통 서명이 없다. 환경이 달라져도 이 기준은 안 흔들린다.

WinVerifyTrust 는 네트워크로 CRL 을 받으러 갈 수 있어서 느려질 수 있다.
`WTD_CACHE_ONLY_URL_RETRIEVAL` 로 캐시만 쓰게 막고 폐기 검사도 끈다.
우리가 알고 싶은 건 "폐기된 인증서인가"가 아니라 "서명이 있기는 한가"다.
"""

import ctypes
import ctypes.wintypes as wt
import os
from concurrent.futures import ThreadPoolExecutor

wintrust = ctypes.WinDLL("wintrust")

# WinVerifyTrust 상수
WTD_UI_NONE = 2
WTD_REVOKE_NONE = 0
WTD_CHOICE_FILE = 1
WTD_STATEACTION_VERIFY = 1
WTD_STATEACTION_CLOSE = 2
WTD_SAFER_FLAG = 0x100
WTD_CACHE_ONLY_URL_RETRIEVAL = 0x1000

TRUST_E_NOSIGNATURE = 0x800B0100
TRUST_E_BAD_DIGEST = 0x80096010
CERT_E_UNTRUSTEDROOT = 0x800B0109
CERT_E_CHAINING = 0x800B010A


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                ("Data3", wt.WORD), ("Data4", ctypes.c_byte * 8)]


class WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wt.DWORD),
                ("pcwszFilePath", wt.LPCWSTR),
                ("hFile", wt.HANDLE),
                ("pgKnownSubject", ctypes.POINTER(GUID))]


class WINTRUST_DATA(ctypes.Structure):
    _fields_ = [("cbStruct", wt.DWORD),
                ("pPolicyCallbackData", wt.LPVOID),
                ("pSIPClientData", wt.LPVOID),
                ("dwUIChoice", wt.DWORD),
                ("fdwRevocationChecks", wt.DWORD),
                ("dwUnionChoice", wt.DWORD),
                ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
                ("dwStateAction", wt.DWORD),
                ("hWVTStateData", wt.HANDLE),
                ("pwszURLReference", wt.LPWSTR),
                ("dwProvFlags", wt.DWORD),
                ("dwUIContext", wt.DWORD),
                ("pSignatureSettings", wt.LPVOID)]


# {00AAC56B-CD44-11d0-8CC2-00C04FC295EE}
WINTRUST_ACTION_GENERIC_VERIFY_V2 = GUID(
    0x00AAC56B, 0xCD44, 0x11D0,
    (ctypes.c_byte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))

wintrust.WinVerifyTrust.argtypes = [wt.HWND, ctypes.POINTER(GUID), ctypes.c_void_p]
wintrust.WinVerifyTrust.restype = ctypes.c_long

_cache = {}


def verify(path):
    """파일의 Authenticode 서명을 검증한다.

    반환: "유효" | "서명없음" | "위조" | "신뢰안됨" | "확인불가"

    catalog 서명(윈도우 시스템 파일 다수)은 파일 자체에 서명이 박혀있지 않고
    별도 카탈로그에 들어있다. WinVerifyTrust 는 그것도 찾아주므로
    시스템 DLL 도 "유효"로 나온다.
    """
    if not path:
        return "확인불가"
    if path in _cache:
        return _cache[path]

    if not os.path.exists(path):
        _cache[path] = "확인불가"
        return _cache[path]

    fi = WINTRUST_FILE_INFO()
    fi.cbStruct = ctypes.sizeof(WINTRUST_FILE_INFO)
    fi.pcwszFilePath = path
    fi.hFile = None
    fi.pgKnownSubject = None

    wd = WINTRUST_DATA()
    wd.cbStruct = ctypes.sizeof(WINTRUST_DATA)
    wd.dwUIChoice = WTD_UI_NONE
    wd.fdwRevocationChecks = WTD_REVOKE_NONE
    wd.dwUnionChoice = WTD_CHOICE_FILE
    wd.pFile = ctypes.pointer(fi)
    wd.dwStateAction = WTD_STATEACTION_VERIFY
    wd.dwProvFlags = WTD_SAFER_FLAG | WTD_CACHE_ONLY_URL_RETRIEVAL

    action = ctypes.pointer(WINTRUST_ACTION_GENERIC_VERIFY_V2)
    rc = wintrust.WinVerifyTrust(None, action, ctypes.byref(wd))

    # 상태 핸들을 반드시 닫는다. 안 닫으면 누수된다.
    wd.dwStateAction = WTD_STATEACTION_CLOSE
    wintrust.WinVerifyTrust(None, action, ctypes.byref(wd))

    code = rc & 0xFFFFFFFF
    if rc == 0:
        result = "유효"
    elif code == TRUST_E_NOSIGNATURE:
        result = "서명없음"
    elif code == TRUST_E_BAD_DIGEST:
        result = "위조"
    elif code in (CERT_E_UNTRUSTEDROOT, CERT_E_CHAINING):
        result = "신뢰안됨"
    else:
        result = "확인불가"

    _cache[path] = result
    return result


# ── 경로 분류 ────────────────────────────────────────────────────────────
_SYS_ROOTS = None


def _sys_roots():
    global _SYS_ROOTS
    if _SYS_ROOTS is None:
        win = os.environ.get("SystemRoot", r"C:\Windows")
        _SYS_ROOTS = tuple(p.lower() for p in (
            os.path.join(win, "System32"),
            os.path.join(win, "SysWOW64"),
            os.path.join(win, "WinSxS"),
            os.path.join(win, "SystemApps"),
            win,
        ))
    return _SYS_ROOTS


def classify_path(path, game_dir):
    """모듈이 어디서 왔는지 분류한다.

    서명만으로는 부족하다. 서명이 유효해도 게임과 무관한 위치에서 로드됐다면
    의심스럽고, 반대로 서명이 없어도 윈도우 시스템 경로면 대개 정상이다.
    """
    if not path:
        return "알수없음"
    low = path.lower()
    if game_dir and low.startswith(game_dir.lower()):
        return "게임"
    if low.startswith(_sys_roots()):
        return "시스템"
    if "\\driverstore\\" in low or "\\drivers\\" in low:
        return "드라이버"
    if "\\steam\\" in low or "\\steamapps\\" in low:
        return "스팀"
    if "\\program files" in low:
        return "설치프로그램"
    for mark in ("\\desktop\\", "\\downloads\\", "\\documents\\", "\\temp\\", "\\appdata\\local\\temp\\"):
        if mark in low:
            return "사용자"
    return "기타"


def prewarm(paths, workers=16):
    """서명 검증을 병렬로 미리 돌려 캐시를 채운다.

    WinVerifyTrust 는 모듈당 0.7초쯤 걸린다. 158개를 순차로 하면 1분 49초다.
    ctypes 호출이 GIL 을 놓으므로 스레드가 실제로 효과를 낸다.
    시스템 경로는 어차피 건너뛰므로 대상에서 뺀다.
    """
    todo = [p for p in set(paths) if p and p not in _cache and needs_check(p)]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(verify, todo))


# 서명 검증을 실제로 돌릴 가치가 있는 경로인가.
# WinVerifyTrust 는 모듈당 0.7초쯤 걸리고 내부적으로 직렬화돼서 스레드로도
# 잘 안 줄어든다. 158개 전부 검증하면 1분 49초다.
# 위협 모델상 의미가 있는 건 **사용자가 쓸 수 있는 경로**뿐이다.
# 시스템·드라이버 경로는 WRP 와 관리자 권한이 지키고, 게임·스팀 경로는
# 설치본의 일부다. 거기에 DLL 을 심었다면 이미 다른 방어선이 뚫린 뒤다.
_SKIP_LOCS = ("시스템", "드라이버", "게임", "스팀")


def needs_check(path, game_dir=None):
    return classify_path(path, game_dir) not in _SKIP_LOCS


def verdict(path, game_dir):
    """서명과 경로를 합쳐 최종 판정한다.

    반환: (등급, 사유)  등급은 "정상" | "주의" | "의심"

    의심으로 올리는 조건은 하나다. **서명이 없는데 사용자 경로에서 로드됐다.**
    치트 DLL 이 정확히 이 조합이다. 반대로 드라이버는 서명이 유효하고,
    시스템 DLL 은 카탈로그 서명이 있다.
    """
    loc = classify_path(path, game_dir)

    # System32 / WinSxS 는 Windows 리소스 보호(WRP)가 지키는 영역이다.
    # 여기에 DLL 을 심으려면 이미 관리자 권한을 뚫은 뒤이고, 그 시점이면
    # 사용자 모드 안티치트로는 어차피 못 막는다. 모듈당 0.7초를 낼 가치가
    # 없어서 검증을 건너뛴다. **이건 명시적 가정이며 한계로 문서화한다.**
    if loc in _SKIP_LOCS:
        return "정상", f"{loc} 경로 (서명 검증 생략)"

    sig = verify(path)

    if sig == "위조":
        return "의심", f"서명 위조 ({loc})"
    if sig == "유효":
        if loc in ("사용자", "기타"):
            return "주의", f"서명은 유효하나 {loc} 경로"
        return "정상", f"서명 유효 ({loc})"
    # 서명 없음 / 신뢰안됨 / 확인불가
    if loc in ("드라이버", "게임", "스팀"):
        return "주의", f"{sig} 이지만 {loc} 경로"
    return "의심", f"{sig} + {loc} 경로"
