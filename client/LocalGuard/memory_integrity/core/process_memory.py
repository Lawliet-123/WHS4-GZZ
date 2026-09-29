import ctypes
import struct
from ctypes import wintypes


# ============================================================
# Windows constants
# ============================================================

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010

MAX_PATH = 260
MAX_MODULE_NAME32 = 255

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


# ============================================================
# Windows structures
# ============================================================

ULONG_PTR = ctypes.c_size_t


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ULONG_PTR),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        (
            "szModule",
            wintypes.WCHAR * (MAX_MODULE_NAME32 + 1),
        ),
        ("szExePath", wintypes.WCHAR * MAX_PATH),
    ]


# ============================================================
# WinAPI
# ============================================================

kernel32 = ctypes.WinDLL(
    "kernel32",
    use_last_error=True,
)


kernel32.CreateToolhelp32Snapshot.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE


kernel32.Process32FirstW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(PROCESSENTRY32W),
]
kernel32.Process32FirstW.restype = wintypes.BOOL


kernel32.Process32NextW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(PROCESSENTRY32W),
]
kernel32.Process32NextW.restype = wintypes.BOOL


kernel32.Module32FirstW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(MODULEENTRY32W),
]
kernel32.Module32FirstW.restype = wintypes.BOOL


kernel32.Module32NextW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(MODULEENTRY32W),
]
kernel32.Module32NextW.restype = wintypes.BOOL


kernel32.OpenProcess.argtypes = [
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]
kernel32.OpenProcess.restype = wintypes.HANDLE


kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL


kernel32.CloseHandle.argtypes = [
    wintypes.HANDLE,
]
kernel32.CloseHandle.restype = wintypes.BOOL


# ============================================================
# ProcessMemory
# ============================================================

class ProcessMemory:
    """
    MECCHA LocalGuard용 읽기 전용 프로세스 메모리 접근 클래스.

    기능:
    - 프로세스 PID 탐색
    - OpenProcess 연결
    - 모듈 Base Address 탐색
    - 메모리 읽기

    메모리를 수정하는 기능은 포함하지 않는다.
    """

    def __init__(self, process_name):
        self.process_name = process_name
        self.pid = None
        self.handle = None
        self.module_base = None

    @staticmethod
    def _raise_last_error(message):
        error_code = ctypes.get_last_error()

        raise OSError(
            error_code,
            f"{message} (WinError {error_code})",
        )

    # ========================================================
    # PID 찾기
    # ========================================================

    @staticmethod
    def find_process_id(process_name):
        snapshot = kernel32.CreateToolhelp32Snapshot(
            TH32CS_SNAPPROCESS,
            0,
        )

        if snapshot == INVALID_HANDLE_VALUE:
            ProcessMemory._raise_last_error(
                "CreateToolhelp32Snapshot failed"
            )

        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)

            success = kernel32.Process32FirstW(
                snapshot,
                ctypes.byref(entry),
            )

            while success:
                if (
                    entry.szExeFile.casefold()
                    == process_name.casefold()
                ):
                    return entry.th32ProcessID

                success = kernel32.Process32NextW(
                    snapshot,
                    ctypes.byref(entry),
                )

        finally:
            kernel32.CloseHandle(snapshot)

        return None

    # ========================================================
    # Module Base Address 찾기
    # ========================================================

    @staticmethod
    def find_module_base(pid, module_name):
        snapshot = kernel32.CreateToolhelp32Snapshot(
            TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32,
            pid,
        )

        if snapshot == INVALID_HANDLE_VALUE:
            ProcessMemory._raise_last_error(
                "Module snapshot failed"
            )

        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(MODULEENTRY32W)

            success = kernel32.Module32FirstW(
                snapshot,
                ctypes.byref(entry),
            )

            while success:
                if (
                    entry.szModule.casefold()
                    == module_name.casefold()
                ):
                    return int(entry.modBaseAddr)

                success = kernel32.Module32NextW(
                    snapshot,
                    ctypes.byref(entry),
                )

        finally:
            kernel32.CloseHandle(snapshot)

        return None

    # ========================================================
    # 연결
    # ========================================================

    def connect(self):
        if self.handle:
            return

        self.pid = self.find_process_id(
            self.process_name
        )

        if self.pid is None:
            raise RuntimeError(
                f"Process not found: {self.process_name}"
            )

        access = (
            PROCESS_QUERY_INFORMATION
            | PROCESS_VM_READ
        )

        self.handle = kernel32.OpenProcess(
            access,
            False,
            self.pid,
        )

        if not self.handle:
            self._raise_last_error(
                "OpenProcess failed"
            )

        self.module_base = self.find_module_base(
            self.pid,
            self.process_name,
        )

        if self.module_base is None:
            self.close()

            raise RuntimeError(
                f"Module not found: {self.process_name}"
            )

    # ========================================================
    # 연결 종료
    # ========================================================

    def close(self):
        if self.handle:
            kernel32.CloseHandle(
                self.handle
            )

        self.handle = None
        self.pid = None
        self.module_base = None

    # ========================================================
    # Raw Memory
    # ========================================================

    def read_bytes(self, address, size):
        if not self.handle:
            raise RuntimeError(
                "Process is not connected"
            )

        if size <= 0:
            raise ValueError(
                "size must be greater than 0"
            )

        buffer = ctypes.create_string_buffer(
            size
        )

        bytes_read = ctypes.c_size_t()

        success = kernel32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(bytes_read),
        )

        if not success:
            self._raise_last_error(
                f"ReadProcessMemory failed at 0x{address:X}"
            )

        if bytes_read.value != size:
            raise RuntimeError(
                f"Partial memory read at 0x{address:X}: "
                f"{bytes_read.value}/{size}"
            )

        return buffer.raw

    # ========================================================
    # Primitive readers
    # ========================================================

    def read_uint8(self, address):
        return struct.unpack(
            "<B",
            self.read_bytes(address, 1),
        )[0]

    def read_bool(self, address):
        return self.read_uint8(address) != 0

    def read_int32(self, address):
        return struct.unpack(
            "<i",
            self.read_bytes(address, 4),
        )[0]

    def read_uint32(self, address):
        return struct.unpack(
            "<I",
            self.read_bytes(address, 4),
        )[0]

    def read_int64(self, address):
        return struct.unpack(
            "<q",
            self.read_bytes(address, 8),
        )[0]

    def read_uint64(self, address):
        return struct.unpack(
            "<Q",
            self.read_bytes(address, 8),
        )[0]

    def read_float(self, address):
        return struct.unpack(
            "<f",
            self.read_bytes(address, 4),
        )[0]

    def read_double(self, address):
        return struct.unpack(
            "<d",
            self.read_bytes(address, 8),
        )[0]

    def read_pointer(self, address):
        return self.read_uint64(address)

    # ========================================================
    # Context Manager
    # ========================================================

    def __enter__(self):
        self.connect()
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        self.close()