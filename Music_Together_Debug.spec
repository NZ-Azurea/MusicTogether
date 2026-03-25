# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src\\main.py'],
    pathex=['src/Python_API'],
    binaries=[],
    datas=[('src/web', 'web'), ('src/Python_API', 'Python_API'), ('Asset', 'Asset')],
    hiddenimports=['Music_Together_API', 'json_loader', 'link_handler', 'urllib', 'urllib.request', 'urllib3', 'miniupnpc', 'psutil', 'uvicorn', 'fastapi', 'websockets', 'pydantic'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Music_Together_Debug',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['Asset\\logo.ico'],
)
