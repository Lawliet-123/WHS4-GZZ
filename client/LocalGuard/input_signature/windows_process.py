"""Read-only local Windows process selection. Never enables debug privileges."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import sys


def api():
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
    _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                ('th32DefaultHeapID', c.c_size_t), ('th32ModuleID', w.DWORD),
                ('cntThreads', w.DWORD), ('th32ParentProcessID', w.DWORD),
                ('pcPriClassBase', w.LONG), ('dwFlags', w.DWORD), ('szExeFile', w.WCHAR * 260)]


class ModuleInfo(c.Structure):
    _fields_ = [('lpBaseOfDll', c.c_void_p), ('SizeOfImage', w.DWORD),
                ('EntryPoint', c.c_void_p)]


def list_process_modules(pid):
    """List loaded image modules of an explicitly selected local process."""
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
    """Read mapped module bytes, not the file on disk; never writes target memory."""
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
    """Return a process snapshot without opening or reading process memory."""
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
    return [row['pid'] for row in list_processes()
            if row['name'].casefold() == name.casefold()]


def process_session_id(pid):
    """Return the Windows session ID for a live PID."""
    if type(pid) is not int or pid <= 0: raise ValueError('positive PID required')
    k = api()
    k.ProcessIdToSessionId.argtypes = [w.DWORD, c.POINTER(w.DWORD)]
    k.ProcessIdToSessionId.restype = w.BOOL
    session = w.DWORD()
    if not k.ProcessIdToSessionId(pid, c.byref(session)):
        raise c.WinError(c.get_last_error())
    return int(session.value)


def process_session_snapshot():
    """Best-effort PID -> Windows session map from SystemProcessInformation.

    Unlike ProcessIdToSessionId per PID, this does not need to open every
    protected process. The native information class can change across Windows
    releases, so malformed or unavailable data raises instead of guessing.
    """
    if sys.platform != 'win32' or c.sizeof(c.c_void_p) != 8:
        raise RuntimeError('Windows 64-bit Python is required')
    n = c.WinDLL('ntdll')
    n.NtQuerySystemInformation.argtypes = [w.ULONG, c.c_void_p, w.ULONG,
                                            c.POINTER(w.ULONG)]
    n.NtQuerySystemInformation.restype = w.LONG
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
    def __init__(self, pid):
        if type(pid) is not int or pid <= 0: raise ValueError('positive PID required')
        self.k = api(); self.pid = pid; self.handle = self.k.OpenProcess(0x1000, False, pid)
        if not self.handle: raise c.WinError(c.get_last_error())
        try:
            self.initial = self.read()
        except BaseException:
            self.close(); raise

    def read(self):
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
        if self.read() != self.initial: raise RuntimeError('process_identity_changed')

    def close(self):
        if self.handle: self.k.CloseHandle(self.handle); self.handle = None

    def __enter__(self): return self
    def __exit__(self, *args): self.close()
