"""Pinned-driver install with explicitly selected test-environment preparation."""
import os
import json
from pathlib import Path
import re
import subprocess
import ctypes


def test_signing_active():
    class CodeIntegrity(ctypes.Structure):
        _fields_=[('Length',ctypes.c_uint32),('Options',ctypes.c_uint32)]
    info=CodeIntegrity(ctypes.sizeof(CodeIntegrity),0)
    returned=ctypes.c_uint32()
    query=ctypes.WinDLL('ntdll').NtQuerySystemInformation
    query.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p]
    query.restype=ctypes.c_int32
    status=query(103,ctypes.byref(info),ctypes.sizeof(info),ctypes.byref(returned))
    if status != 0:
        raise RuntimeError(f'Code Integrity state query failed (NTSTATUS=0x{status & 0xffffffff:08x})')
    return bool(info.Options & 0x02)


def prepare_test_environment(source, sha256):
    root=Path(__file__).resolve().parents[1]
    script=root/'scripts/PrepareTestEnvironment.ps1'
    # This is the public certificate extracted from the user-supplied signed SYS.
    thumbprint='AC82C79E947F598200D81C59819EA312A9CDA87C'
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(script),
        '-SysPath',str(source),'-ExpectedSha256',sha256,
        '-CertificatePath',str(root/'artifacts/KernelSentinel-test.cer'),
        '-CertificateThumbprint',thumbprint,'-EffectiveTestSigning',str(test_signing_active()).lower()],check=False)
    if result.returncode == 3010:
        raise RuntimeError('TEST_SIGNING_REBOOT_REQUIRED: save work and restart Windows, then run Launcher again')
    if result.returncode:
        raise RuntimeError(f'Test-environment setup failed (exit {result.returncode}); driver was not installed')


def ensure_driver(path=None, sha256=None, *, prepare_test=False):
    if os.name != 'nt':
        raise RuntimeError('Automatic driver installation requires Windows')
    path = path or os.environ.get('GZZ_KERNEL_DRIVER_PATH')
    sha256 = sha256 or os.environ.get('GZZ_KERNEL_DRIVER_SHA256', '')
    if not path and not sha256:
        # Bundled release has a pinned manifest, never derives its own approval hash.
        artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
        manifest_path = artifacts / 'driver_manifest.json'
        if not manifest_path.is_file():
            raise ValueError('Bundled driver manifest missing; use the deployment ZIP or set explicit driver path/hash')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('filename') != 'KernelSentinel.sys':
            raise ValueError('Unexpected bundled SYS filename')
        path = str(artifacts / manifest['filename'])
        sha256 = manifest.get('sha256', '')
    if not path or not re.fullmatch(r'[a-fA-F0-9]{64}', sha256):
        raise ValueError('Set GZZ_KERNEL_DRIVER_PATH and approved GZZ_KERNEL_DRIVER_SHA256 before auto-install')
    source = Path(path).resolve(strict=True)
    if prepare_test:
        prepare_test_environment(source, sha256)
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'EnsureDriver.ps1'
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(script),
                             '-SysPath', str(source), '-ExpectedSha256', sha256], check=False)
    if result.returncode:
        raise RuntimeError(f'Approved driver setup failed (exit {result.returncode})')
