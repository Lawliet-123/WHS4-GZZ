"""Windows 프로세스와 로드 모듈을 읽기 전용으로 확인하는 Win32 래퍼.

핵심 목적은 검사 전후에 PID가 같은 프로세스를 가리키는지 확인하는 것이다.
디버그 권한을 켜거나 대상 프로세스에 쓰기 권한을 요청하지 않는다.
"""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import sys


def api():
    """64비트 Windows를 확인하고 사용하는 kernel32 함수의 타입을 지정한다."""
    if sys.platform != 'win32' or c.sizeof(c.c_void_p) != 8:
        raise RuntimeError('Windows 64-bit Python is required')
    k = c.WinDLL('kernel32', use_last_error=True)
    k.CloseHandle.argtypes = [w.HANDLE]; k.CloseHandle.restype = w.BOOL
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]; k.OpenProcess.restype = w.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    k.QueryFullProcessImageNameW.restype = w.BOOL
    k.GetProcessTimes.argtypes = [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4
    k.GetProcessTimes.restype = w.BOOL
    return k


class Entry(c.Structure):
    """Toolhelp 프로세스 스냅샷이 채우는 PROCESSENTRY32W 구조체."""
    _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                ('th32DefaultHeapID', c.c_size_t), ('th32ModuleID', w.DWORD),
                ('cntThreads', w.DWORD), ('th32ParentProcessID', w.DWORD),
                ('pcPriClassBase', w.LONG), ('dwFlags', w.DWORD), ('szExeFile', w.WCHAR * 260)]


class WtsProcessInfo(c.Structure):
    """WTS_PROCESS_INFOW: 프로세스 소유자를 이름이 아닌 Windows SID로 구분한다."""
    _fields_ = [('SessionId', w.DWORD), ('ProcessId', w.DWORD),
                ('pProcessName', w.LPWSTR), ('pUserSid', c.c_void_p)]


class ModuleInfo(c.Structure):
    """PSAPI가 돌려주는 모듈의 메모리 시작 주소·이미지 크기."""
    _fields_ = [('lpBaseOfDll', c.c_void_p), ('SizeOfImage', w.DWORD),
                ('EntryPoint', c.c_void_p)]


def list_process_modules(pid):
    """지정한 PID에 실제 로드된 이미지 모듈의 이름·범위를 나열한다.

    프로세스 정보 조회와 읽기 권한만 요청한다. 목록 도중 언로드된 모듈은
    건너뛰므로 호출자는 스캔 직후에도 로드 여부를 다시 확인해야 한다.
    """
    if type(pid) is not int or pid <= 0: raise ValueError('positive PID required')
    k = api()
    p = c.WinDLL('psapi', use_last_error=True)
    p.EnumProcessModulesEx.argtypes = [w.HANDLE, c.POINTER(w.HMODULE), w.DWORD,
                                       c.POINTER(w.DWORD), w.DWORD]
    p.EnumProcessModulesEx.restype = w.BOOL
    p.GetModuleFileNameExW.argtypes = [w.HANDLE, w.HMODULE, w.LPWSTR, w.DWORD]
    p.GetModuleFileNameExW.restype = w.DWORD
    p.GetModuleInformation.argtypes = [w.HANDLE, w.HMODULE,
                                       c.POINTER(ModuleInfo), w.DWORD]
    p.GetModuleInformation.restype = w.BOOL
    handle = k.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle: raise c.WinError(c.get_last_error())
    modules = []
    try:
        # 모듈 수가 첫 버퍼보다 많으면 필요한 개수로 다시 할당한다.
        capacity = 256
        while True:
            handles = (w.HMODULE * capacity)()
            needed = w.DWORD()
            if not p.EnumProcessModulesEx(handle, handles, c.sizeof(handles),
                                          c.byref(needed), 0x03):
                raise c.WinError(c.get_last_error())
            count = needed.value // c.sizeof(w.HMODULE)
            if count <= capacity: break
            if count > 4096: raise RuntimeError('too_many_loaded_modules')
            capacity = count
        for module_handle in handles[:count]:
            name = c.create_unicode_buffer(32768)
            if not p.GetModuleFileNameExW(handle, module_handle, name, len(name)):
                continue  # A module may have unloaded during enumeration.
            info = ModuleInfo()
            if not p.GetModuleInformation(handle, module_handle, c.byref(info), c.sizeof(info)):
                continue
            modules.append({'name': Path(name.value).name, 'path': name.value,
                            'base_address': int(info.lpBaseOfDll),
                            'size': int(info.SizeOfImage)})
    finally:
        k.CloseHandle(handle)
    return modules


def read_process_module(pid, module, *, max_bytes=16 * 1024 * 1024):
    """디스크 파일이 아닌 대상 프로세스의 매핑된 모듈 바이트를 읽는다.

    상한을 넘는 범위나 부분 읽기는 실패로 처리한다. 읽기 전용 핸들을 열고
    완료 여부와 관계없이 닫는다. DLL이 실제로 사용 중인지까지 증명하지는 않는다.
    """
    if type(pid) is not int or pid <= 0: raise ValueError('positive PID required')
    size = module['size']; base = module['base_address']
    if (type(size) is not int or type(base) is not int or
            not 0 < size <= max_bytes or base <= 0):
        raise ValueError('invalid or oversized module memory region')
    k = api()
    k.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p,
                                     c.c_size_t, c.POINTER(c.c_size_t)]
    k.ReadProcessMemory.restype = w.BOOL
    handle = k.OpenProcess(0x1000 | 0x0010, False, pid)
    if not handle: raise c.WinError(c.get_last_error())
    try:
        buffer = c.create_string_buffer(size)
        count = c.c_size_t()
        if not k.ReadProcessMemory(handle, c.c_void_p(base), buffer, size, c.byref(count)):
            raise c.WinError(c.get_last_error())
        if count.value != size: raise RuntimeError('short_module_memory_read')
        return buffer.raw
    finally:
        k.CloseHandle(handle)


def list_processes():
    """Toolhelp 스냅샷으로 PID·이름·부모 PID만 가져온다.

    이 단계에서는 어떤 프로세스 메모리도 열지 않는다. 목록은 순간 관측이라
    이후 PID를 쓰기 전 ``ProcessIdentity`` 확인이 필요하다.
    """
    k = api()
    k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    k.CreateToolhelp32Snapshot.restype = w.HANDLE
    for method in ('Process32FirstW', 'Process32NextW'):
        fn = getattr(k, method); fn.argtypes = [w.HANDLE, c.POINTER(Entry)]; fn.restype = w.BOOL
    handle = k.CreateToolhelp32Snapshot(2, 0)
    if handle == c.c_void_p(-1).value: raise c.WinError(c.get_last_error())
    found = []
    try:
        entry = Entry(); entry.dwSize = c.sizeof(Entry)
        c.set_last_error(0)
        ok = k.Process32FirstW(handle, c.byref(entry))
        while ok:
            found.append({'pid': int(entry.th32ProcessID),
                          'name': str(entry.szExeFile),
                          'parent_pid': int(entry.th32ParentProcessID)})
            ok = k.Process32NextW(handle, c.byref(entry))
        error = c.get_last_error()
        if error not in (0, 18): raise c.WinError(error)
    finally:
        k.CloseHandle(handle)
    return found


def find_processes(name):
    """대소문자 차이를 무시하고 실행 파일명이 같은 PID들을 찾는다."""
    return [row['pid'] for row in list_processes()
            if row['name'].casefold() == name.casefold()]


def process_session_id(pid):
    """현재 살아 있는 PID의 Windows 로그인 세션 ID를 얻는다."""
    if type(pid) is not int or pid <= 0: raise ValueError('positive PID required')
    k = api()
    k.ProcessIdToSessionId.argtypes = [w.DWORD, c.POINTER(w.DWORD)]
    k.ProcessIdToSessionId.restype = w.BOOL
    session = w.DWORD()
    if not k.ProcessIdToSessionId(pid, c.byref(session)):
        raise c.WinError(c.get_last_error())
    return int(session.value)


def process_owner_snapshot():
    """PID→소유자 SID 스냅샷을 만든다. 조회 불가 SID는 None으로 남긴다.

    WTS는 보호된 프로세스의 SID를 제공하지 않을 수 있다. None을 게임 계정의
    SID로 추측하거나 프로세스 이름을 근거로 소유자를 결정하지 않는다.
    반환하는 SID 바이트는 메모리 내 비교에만 쓰며 로그에 기록하지 않는다.
    """
    if sys.platform != 'win32' or c.sizeof(c.c_void_p) != 8:
        raise RuntimeError('Windows 64-bit Python is required')
    wts = c.WinDLL('wtsapi32', use_last_error=True)
    security = c.WinDLL('advapi32', use_last_error=True)
    wts.WTSEnumerateProcessesW.argtypes = [w.HANDLE, w.DWORD, w.DWORD,
                                           c.POINTER(c.POINTER(WtsProcessInfo)),
                                           c.POINTER(w.DWORD)]
    wts.WTSEnumerateProcessesW.restype = w.BOOL
    wts.WTSFreeMemory.argtypes = [c.c_void_p]
    wts.WTSFreeMemory.restype = None
    security.IsValidSid.argtypes = [c.c_void_p]
    security.IsValidSid.restype = w.BOOL
    security.GetLengthSid.argtypes = [c.c_void_p]
    security.GetLengthSid.restype = w.DWORD
    rows = c.POINTER(WtsProcessInfo)()
    count = w.DWORD()
    # WTS_CURRENT_SERVER_HANDLE=0, Version=1. 버퍼는 반드시 WTSFreeMemory로 해제.
    if not wts.WTSEnumerateProcessesW(None, 0, 1, c.byref(rows), c.byref(count)):
        raise c.WinError(c.get_last_error())
    try:
        owners = {}
        for index in range(count.value):
            item = rows[index]
            if not item.ProcessId:
                continue
            sid = None
            if item.pUserSid:
                if not security.IsValidSid(item.pUserSid):
                    raise RuntimeError('process_owner_snapshot_invalid_sid')
                length = security.GetLengthSid(item.pUserSid)
                if not 8 <= length <= 68:
                    raise RuntimeError('process_owner_snapshot_invalid_sid_length')
                sid = c.string_at(item.pUserSid, length)
            owners[int(item.ProcessId)] = sid
        return owners
    finally:
        wts.WTSFreeMemory(rows)


def process_session_snapshot():
    """시스템 정보 한 번으로 PID→Windows 세션 표를 만든다.

    개별 보호 프로세스를 모두 열지 않아도 되지만, 사용하는 네이티브 구조의
    오프셋은 Windows 버전에 따라 바뀔 수 있다. 응답 길이·다음 항목 오프셋이
    예상과 다르면 추측하지 않고 오류를 낸다.
    """
    if sys.platform != 'win32' or c.sizeof(c.c_void_p) != 8:
        raise RuntimeError('Windows 64-bit Python is required')
    n = c.WinDLL('ntdll')
    n.NtQuerySystemInformation.argtypes = [w.ULONG, c.c_void_p, w.ULONG,
                                            c.POINTER(w.ULONG)]
    n.NtQuerySystemInformation.restype = w.LONG
    # STATUS_INFO_LENGTH_MISMATCH일 때만 버퍼를 키우고 최대 32 MiB로 제한한다.
    size = 1024 * 1024
    while size <= 32 * 1024 * 1024:
        buffer = c.create_string_buffer(size)
        needed = w.ULONG()
        status = n.NtQuerySystemInformation(5, buffer, size, c.byref(needed))
        if status >= 0:
            break
        if status & 0xFFFFFFFF != 0xC0000004:
            raise OSError('process_session_snapshot_ntstatus_0x%08x' %
                          (status & 0xFFFFFFFF))
        size = max(size * 2, needed.value + 65536)
    else:
        raise RuntimeError('process_session_snapshot_size_limit')
    length = needed.value or size
    if length > size or length < 104:
        raise RuntimeError('process_session_snapshot_invalid_length')
    found = {}
    offset = 0
    # 각 레코드의 NextEntryOffset을 따라간다. 잘못된 오프셋은 무한 루프나
    # 범위 밖 읽기로 이어질 수 있어 아래에서 명시적으로 거부한다.
    while True:
        if offset + 104 > length:
            raise RuntimeError('process_session_snapshot_truncated')
        next_offset = c.c_uint32.from_buffer_copy(buffer, offset).value
        pid = c.c_size_t.from_buffer_copy(buffer, offset + 80).value
        session = c.c_uint32.from_buffer_copy(buffer, offset + 100).value
        if pid > 0:
            found[int(pid)] = int(session)
        if next_offset == 0:
            break
        if next_offset < 104 or offset + next_offset <= offset or offset + next_offset > length:
            raise RuntimeError('process_session_snapshot_invalid_offset')
        offset += next_offset
    return found


class ProcessIdentity:
    """PID·이미지 경로·생성 시각을 묶어 PID 재사용을 탐지한다."""
    def __init__(self, pid):
        """대상에 최소 조회 권한만 요청하고 최초 프로세스 지문을 저장한다."""
        if type(pid) is not int or pid <= 0: raise ValueError('positive PID required')
        self.k = api(); self.pid = pid; self.handle = self.k.OpenProcess(0x1000, False, pid)
        if not self.handle: raise c.WinError(c.get_last_error())
        try:
            self.initial = self.read()
        except BaseException:
            self.close(); raise

    def read(self):
        """열어 둔 핸들에서 현재 이미지 경로·생성 시각·종료 여부를 읽는다."""
        path = c.create_unicode_buffer(32768); size = w.DWORD(len(path))
        if not self.k.QueryFullProcessImageNameW(self.handle, 0, path, c.byref(size)):
            raise c.WinError(c.get_last_error())
        times = [w.FILETIME() for _ in range(4)]
        if not self.k.GetProcessTimes(self.handle, *(c.byref(t) for t in times)):
            raise c.WinError(c.get_last_error())
        values = [(t.dwHighDateTime << 32) | t.dwLowDateTime for t in times]
        if values[1]: raise RuntimeError('target_process_exited')
        return {'pid': self.pid, 'image_path': path.value, 'creation_time_100ns': values[0]}

    def check(self):
        """최초 지문과 달라졌거나 프로세스가 종료됐으면 검사를 실패시킨다."""
        if self.read() != self.initial: raise RuntimeError('process_identity_changed')

    def close(self):
        """대상 핸들을 중복 닫기 없이 해제한다."""
        if self.handle: self.k.CloseHandle(self.handle); self.handle = None

    def __enter__(self):
        """with 블록에서 프로세스 지문 객체를 사용하도록 반환한다."""
        return self

    def __exit__(self, *args):
        """예외가 나도 프로세스 핸들을 닫는다."""
        self.close()
