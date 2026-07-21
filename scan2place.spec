# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec for a single-file scan2place executable.
# Build:  pyinstaller scan2place.spec   ->  dist/scan2place[.exe]
from PyInstaller.utils.hooks import collect_submodules

# uvicorn picks its loop / http / websocket implementation dynamically ("auto"),
# so every candidate available on the build platform must be bundled explicitly.
# Wrapped in try/except so a package missing on one OS (e.g. uvloop on Windows)
# doesn't break the build there.
_hidden = []
for _pkg in ("uvicorn", "websockets", "wsproto", "h11", "httptools",
             "anyio", "starlette", "fastapi", "multipart", "uvloop"):
    try:
        _hidden += collect_submodules(_pkg)
    except Exception:
        pass

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[("static", "static"), ("templates", "templates")],
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
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
    name="scan2place",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
