"""Owned fixture service for the real driver-load test."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path, PureWindowsPath
import struct
import subprocess
import time


def driver_image_path(path):
    """Driver ImagePath is a file name, not a quoted executable command line.
    subprocess supplies command-line transport quoting for the single argument.
    """
    value = str(path)
    if value.startswith('\\??\\'):
        value = value[4:]
    p = PureWindowsPath(value)
    if '"' in value or '\0' in value or not p.is_absolute() or len(p.drive) != 2 or p.drive[1] != ':':
        raise ValueError('Fixture requires an absolute local drive path without literal quotes')
    return '\\??\\' + str(p)


class ProbeService:
    name = 'KsValidationProbe'

    def __init__(self, path, api, report):
        self.path = Path(path).resolve(strict=True)
        if self.path.name.casefold() != 'ksvalidationprobe.sys':
            raise ValueError('--probe-sys must reference the built KsValidationProbe.sys fixture')
        self.api, self.report = api, report
        self.created = False
        self.sc = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'sc.exe')

    def command(self, *args):
        result = subprocess.run([self.sc, *args], capture_output=True, text=True, errors='replace',
                                timeout=20, creationflags=0x08000000)
        self.report.note(dict(type='fixture_service', args=list(args), exit_code=result.returncode,
                              stdout=result.stdout, stderr=result.stderr))
        return result

    def verify_registered_path(self, expected):
        import winreg
        key_path = 'SYSTEM\\CurrentControlSet\\Services\\' + self.name
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ) as key:
            actual, kind = winreg.QueryValueEx(key, 'ImagePath')
        self.report.note(dict(type='fixture_registered_path', expected=expected, actual=actual, registry_type=kind))
        if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or driver_image_path(actual).casefold() != expected.casefold():
            raise RuntimeError(f'Unexpected registered driver ImagePath: {actual!r}; expected {expected!r}')

    def start(self):
        query = self.command('query', self.name)
        if query.returncode != 1060:
            raise RuntimeError('Fixture service already exists or query failed; no existing service will be modified')
        image_path = driver_image_path(self.path)
        result = self.command('create', self.name, 'type=', 'kernel', 'start=', 'demand', 'binPath=', image_path)
        if result.returncode:
            raise RuntimeError(f'Create fixture service failed: {result.returncode}')
        self.created = True
        self.verify_registered_path(image_path)
        result = self.command('start', self.name)
        if result.returncode:
            hints = {123: 'Invalid driver path syntax; inspect fixture_registered_path in truth.jsonl',
                     577: 'Driver signature rejected; sign this SYS with your VM-trusted test certificate',
                     1275: 'Driver blocked by current Windows policy; inspect Code Integrity logs',
                     5: 'Access denied; use an administrator PowerShell',
                     2: 'Windows could not find the configured SYS file'}
            hint = hints.get(result.returncode, 'Inspect the recorded sc.exe output and Windows System/Code Integrity logs')
            raise RuntimeError(f'Load fixture failed: {result.returncode}; {hint}; SYS={self.path}')
        h = self.api.create(r'\\.\KsValidationProbe', 0x80000000, 0, None, 3, 0, None)
        if h in (None, C.c_void_p(-1).value):
            raise C.WinError(C.get_last_error())
        try:
            out, count = C.create_string_buffer(16), W.DWORD()
            ioctl = (0x8357 << 16) | (1 << 14) | (0x800 << 2)
            if not self.api.control(h, ioctl, None, 0, out, 16, C.byref(count), None):
                raise C.WinError(C.get_last_error())
            if count.value != 16:
                raise RuntimeError('Fixture state size mismatch')
            version, tid, a, b = struct.unpack('<4I', out.raw)
            if version != 1 or not tid or a or b:
                raise RuntimeError('Fixture returned invalid state')
            return tid
        finally:
            self.api.close(h)

    def close(self):
        if not self.created: return
        stopped = self.command('stop', self.name)
        if stopped.returncode not in (0, 1062):
            self.report.add('05.probe_cleanup', 'ERROR', f'Service stop failed: {stopped.returncode}; service retained')
            return
        # Kernel service stops are normally synchronous; verify before deletion.
        for _ in range(30):
            result = self.command('query', self.name)
            if result.returncode == 0 and 'STOPPED' in result.stdout:
                deleted = self.command('delete', self.name)
                self.report.add('05.probe_cleanup', 'PASS' if deleted.returncode == 0 else 'ERROR',
                                f'Fixture stopped; delete exit={deleted.returncode}')
                return
            time.sleep(.1)
        self.report.add('05.probe_cleanup', 'ERROR', 'Stopped state could not be verified; service retained for inspection')
