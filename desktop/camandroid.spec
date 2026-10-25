# PyInstaller spec: builds camandroid.exe (GUI when double-clicked, CLI when given arguments).
# -*- mode: python ; coding: utf-8 -*-
import os
import re
import sys

from PyInstaller.utils.hooks import collect_submodules

from camandroid_desktop import __version__
from camandroid_desktop.icon import save_ico

# Built by CI before packaging: windows/vcam DLLs and Android platform-tools adb.
VCAM_DIR = os.environ.get("CAMANDROID_VCAM_BUILD", "../windows/vcam/dist")
ADB_DIR = os.environ.get("CAMANDROID_ADB_BUILD", "../windows/adb")
datas = [(os.path.join(VCAM_DIR, f), "vcam") for f in ("camandroid-vcam64.dll", "camandroid-vcam32.dll")]
datas += [(os.path.join(ADB_DIR, f), "adb") for f in os.listdir(ADB_DIR)] if os.path.isdir(ADB_DIR) else []
datas = [d for d in datas if os.path.isfile(d[0])]

# Same icon as the Android app; the installer uses this file too.
os.makedirs("build", exist_ok=True)
ICON = os.path.abspath(os.path.join("build", "camandroid.ico"))
save_ico(ICON)


def windows_version_info():
    """File properties shown by Explorer (Details tab)."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = tuple((list(map(int, re.findall(r"\d+", __version__.split("-")[0])))[:4] + [0, 0, 0, 0])[:4])
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Omar Kharrat"),
                StringStruct("FileDescription", "CamAndroid - use your Android phone as a webcam"),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", "camandroid"),
                StringStruct("LegalCopyright", "Copyright (C) 2026 Omar Kharrat (@smayah69)"),
                StringStruct("OriginalFilename", "camandroid.exe"),
                StringStruct("ProductName", "CamAndroid"),
                StringStruct("ProductVersion", __version__),
                StringStruct("Comments", "https://github.com/smayah69/CamAndroid"),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x409, 1200])]),
        ],
    )


a = Analysis(
    ["launcher.py"],
    datas=datas,
    hiddenimports=collect_submodules("zeroconf") + collect_submodules("av"),
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
    icon=ICON,
    version=windows_version_info() if sys.platform == "win32" else None,
)
