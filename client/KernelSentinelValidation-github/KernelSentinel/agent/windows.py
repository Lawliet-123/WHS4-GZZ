"""64-bit ctypes declarations: no pymem and no third-party requirements."""
import ctypes as C
from ctypes import wintypes as W
import os
from . import protocol as P

def bind(dll, name, result, args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, args
    return fn

class Windows:
    def __init__(self):
        if os.name != 'nt' or C.sizeof(C.c_void_p) != 8:
            raise RuntimeError('Windows x64 and 64-bit Python are required')
        k = C.WinDLL('kernel32', use_last_error=True)
        self.close = bind(k, 'CloseHandle', W.BOOL, [W.HANDLE])
        self.open_process = bind(k, 'OpenProcess', W.HANDLE, [W.DWORD, W.BOOL, W.DWORD])
        self.wait = bind(k, 'WaitForSingleObject', W.DWORD, [W.HANDLE, W.DWORD])
        self.times = bind(k, 'GetProcessTimes', W.BOOL, [W.HANDLE] + [C.POINTER(W.FILETIME)]*4)
        self.image = bind(k, 'QueryFullProcessImageNameW', W.BOOL, [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)])
        self.create = bind(k, 'CreateFileW', W.HANDLE, [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE])
        self.control = bind(k, 'DeviceIoControl', W.BOOL, [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD,
            C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p])
        self.query_dos = bind(k, 'QueryDosDeviceW', W.DWORD, [W.LPCWSTR, W.LPWSTR, W.DWORD])
        p = C.WinDLL('psapi', use_last_error=True)
        self.enum_drivers = bind(p, 'EnumDeviceDrivers', W.BOOL, [C.POINTER(C.c_void_p), W.DWORD, C.POINTER(W.DWORD)])
        self.driver_path = bind(p, 'GetDeviceDriverFileNameW', W.DWORD, [C.c_void_p, W.LPWSTR, W.DWORD])

    def process(self, pid):
        # Retain this handle throughout the session. Wait avoids PID-reuse confusion.
        h = self.open_process(0x1000 | 0x100000, False, pid)
        if not h:
            raise C.WinError(C.get_last_error())
        try:
            ft = [W.FILETIME() for _ in range(4)]
            if not self.times(h, *[C.byref(x) for x in ft]):
                raise C.WinError(C.get_last_error())
            buf, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            if not self.image(h, 0, buf, C.byref(size)):
                raise C.WinError(C.get_last_error())
            return h, (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime, buf.value
        except BaseException:
            self.close(h)
            raise

    def drivers(self):
        for capacity in (1024, 4096, 16384):
            values, needed = (C.c_void_p * capacity)(), W.DWORD()
            if not self.enum_drivers(values, C.sizeof(values), C.byref(needed)):
                raise C.WinError(C.get_last_error())
            if needed.value > C.sizeof(values):
                continue
            count = needed.value // C.sizeof(C.c_void_p)
            if count == 0 or any(not values[i] for i in range(count)):
                raise RuntimeError('PSAPI has no usable addresses; check SeDebugPrivilege (not evidence of hiding)')
            result = []
            for i in range(count):
                path = C.create_unicode_buffer(32768)
                n = self.driver_path(values[i], path, len(path))
                result.append({'base': values[i], 'path': path.value if n else '', 'size': None})
            return result
        raise RuntimeError('driver enumeration exceeded limit')

    def enable_debug_privilege(self):
        class LUID(C.Structure):
            _fields_ = [('Low', W.DWORD), ('High', W.LONG)]
        class TP(C.Structure):
            _fields_ = [('Count', W.DWORD), ('Luid', LUID), ('Attributes', W.DWORD)]
        a = C.WinDLL('advapi32', use_last_error=True)
        op = bind(a, 'OpenProcessToken', W.BOOL, [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)])
        lu = bind(a, 'LookupPrivilegeValueW', W.BOOL, [W.LPCWSTR, W.LPCWSTR, C.POINTER(LUID)])
        adj = bind(a, 'AdjustTokenPrivileges', W.BOOL, [W.HANDLE, W.BOOL, C.POINTER(TP), W.DWORD, C.c_void_p, C.c_void_p])
        token, tp = W.HANDLE(), TP()
        if not op(W.HANDLE(-1), 0x20 | 8, C.byref(token)):
            return False
        try:
            tp.Count, tp.Attributes = 1, 2
            if not lu(None, 'SeDebugPrivilege', C.byref(tp.Luid)):
                return False
            C.set_last_error(0)
            return bool(adj(token, False, C.byref(tp), 0, None, None)) and C.get_last_error() == 0
        finally:
            self.close(token)

    def local_path(self, path):
        if path.startswith('\\??\\'):
            path = path[4:]
        if path.lower().startswith('\\systemroot\\'):
            path = os.environ.get('SystemRoot', r'C:\Windows') + path[11:]
        if path.lower().startswith('\\device\\'):
            for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
                buf = C.create_unicode_buffer(4096)
                if self.query_dos(letter + ':', buf, len(buf)):
                    prefix = buf.value
                    if path.lower().startswith(prefix.lower() + '\\'):
                        path = letter + ':' + path[len(prefix):]
                        break
        if len(path) < 3 or path[1:3] != ':\\' or not path[0].isalpha():
            raise ValueError('not a resolved local drive path')
        return path

class Driver:
    def __init__(self, api):
        self.api = api
        self.handle = api.create(r'\\.\KernelSentinel', 0xC0000000, 0, None, 3, 0, None)
        if self.handle in (None, C.c_void_p(-1).value):
            raise C.WinError(C.get_last_error())
    def call(self, code, payload=b'', size=0):
        source = C.create_string_buffer(payload) if payload else None
        target = C.create_string_buffer(size) if size else None
        count = W.DWORD()
        if not self.api.control(self.handle, code, source, len(payload), target, size, C.byref(count), None):
            raise C.WinError(C.get_last_error())
        if count.value > size:
            raise ValueError('invalid returned buffer length')
        return target.raw[:count.value] if target is not None else b''
    def policy(self, pid=0, mode=0, created=0):
        self.call(P.IO_POLICY, P.POLICY.pack(P.VERSION, pid, mode, 0, created))
    def status(self):
        return P.parse_status(self.call(P.IO_STATUS, size=P.STATUS.size))
    def events(self):
        return P.parse_events(self.call(P.IO_EVENTS, size=16+64*P.EVENT.size))
    def diagnostics(self):
        return P.parse_diagnostics(self.call(P.IO_DIAGNOSTICS, size=P.DIAG_SIZE))
    def code_scan(self):
        return P.parse_code(self.call(P.IO_CODE, size=P.CODE.size))
    def thread_scan(self):
        return P.parse_threads(self.call(P.IO_THREADS, size=P.THREAD_SIZE))
    def probe_callback(self, pid):
        handle = self.api.open_process(0x1000, False, pid)
        if not handle:
            return False
        self.api.close(handle)
        return True
    def close(self):
        if self.handle is not None:
            self.api.close(self.handle)
            self.handle = None
