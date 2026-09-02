# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / "app" / "app.py")],
    pathex=[str(root / "app")],
    binaries=[
        (str(root / "app" / "native" / "runtime-bridge.dll"), "native"),
        (str(root / "app" / "native" / "runtime-injector.exe"), "native"),
    ],
    datas=[(str(root / "app" / "mesh-profiles"), "mesh-profiles")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Meccha-Chameleon-LiteV2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
