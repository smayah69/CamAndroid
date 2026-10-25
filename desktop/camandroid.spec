# PyInstaller spec: builds camandroid.exe (GUI when double-clicked, CLI when given arguments).
# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_submodules

# Built by CI before packaging: windows/vcam DLLs and Android platform-tools adb.
VCAM_DIR = os.environ.get("CAMANDROID_VCAM_BUILD", "../windows/vcam/dist")
ADB_DIR = os.environ.get("CAMANDROID_ADB_BUILD", "../windows/adb")
datas = [(os.path.join(VCAM_DIR, f), "vcam") for f in ("camandroid-vcam64.dll", "camandroid-vcam32.dll")]
datas += [(os.path.join(ADB_DIR, f), "adb") for f in os.listdir(ADB_DIR)] if os.path.isdir(ADB_DIR) else []
datas = [d for d in datas if os.path.isfile(d[0])]

a = Analysis(
    ["launcher.py"],
    datas=datas,
    hiddenimports=collect_submodules("zeroconf"),
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
