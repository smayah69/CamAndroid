# PyInstaller spec: builds a single-file `camandroid` executable (GUI by default, CLI with arguments).
# -*- mode: python ; coding: utf-8 -*-
import sys

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
    console=sys.platform != "win32",  # no console window when double-clicked on Windows
    upx=False,
)
if sys.platform == "darwin":
    app = BUNDLE(exe, name="CamAndroid.app", bundle_identifier="com.camandroid.desktop")
