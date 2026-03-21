# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['entry_point.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['requests', 'questionary', 'prompt_toolkit', 'prompt_toolkit.input', 'prompt_toolkit.output', 'prompt_toolkit.styles', 'wcwidth'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'PIL'],
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
    name='jenkins-build',
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
)
