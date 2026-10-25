# PyInstaller spec: builds camandroid.exe (GUI when double-clicked, CLI when given arguments).
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["launcher.py"],
    hiddenimports=collect_submodules("zeroconf") + collect_submodules("pyvirtualcam"),
    excludes=["matplotlib", "scipy", "pandas"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="camandroid",
    console=False,  # no console window when double-clicked
    upx=False,
)
