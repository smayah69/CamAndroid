"""Install, remove and inspect the CamAndroid virtual camera (camandroid-vcam64/32.dll).

The camera is a user-mode COM/DirectShow DLL, not a kernel driver, so no driver signing is
involved. It can be registered for the current user (no admin rights) or for all users
(what the installer does).
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .resources import find_bundled

CLSID = "{F216EFC3-E8A2-425E-A80C-24FBA2B0DB23}"
DLL64 = "camandroid-vcam64.dll"
DLL32 = "camandroid-vcam32.dll"


class CameraInstallError(Exception):
    pass


@dataclass
class CameraStatus:
    x64: str | None  # registered DLL path for 64-bit apps
    x86: str | None  # registered DLL path for 32-bit apps
    scope: str  # "user", "machine", "mixed" or "none"

    @property
    def installed(self) -> bool:
        return self.x64 is not None

    def describe(self) -> str:
        if not self.installed:
            return "CamAndroid camera: not installed"
        bits = "64-bit and 32-bit apps" if self.x86 else "64-bit apps"
        where = {"user": "for this user", "machine": "for all users"}.get(self.scope, "")
        return f"CamAndroid camera: installed {where} ({bits})".replace("  ", " ")


def _registered_path(hive, view: int) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(hive, rf"Software\Classes\CLSID\{CLSID}\InprocServer32", 0,
                            winreg.KEY_READ | view) as key:
            value, _ = winreg.QueryValueEx(key, "")
    except OSError:
        return None
    return value if value and os.path.isfile(value) else None


def status() -> CameraStatus:
    if sys.platform != "win32":
        return CameraStatus(None, None, "none")
    import winreg

    found = {}
    scopes = set()
    for arch, view in (("x64", winreg.KEY_WOW64_64KEY), ("x86", winreg.KEY_WOW64_32KEY)):
        for scope, hive in (("user", winreg.HKEY_CURRENT_USER), ("machine", winreg.HKEY_LOCAL_MACHINE)):
            path = _registered_path(hive, view)
            if path:
                found[arch] = path
                scopes.add(scope)
                break
    scope = "none" if not scopes else (scopes.pop() if len(scopes) == 1 else "mixed")
    return CameraStatus(found.get("x64"), found.get("x86"), scope)


def _regsvr32(bits: int) -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    # From a 32-bit process, System32 is redirected; Sysnative reaches the 64-bit tools.
    if bits == 64:
        native = Path(root) / ("Sysnative" if _is_wow64() else "System32") / "regsvr32.exe"
    else:
        native = Path(root) / ("SysWOW64" if (Path(root) / "SysWOW64").exists() else "System32") / "regsvr32.exe"
    return str(native)


def _is_wow64() -> bool:
    return sys.maxsize <= 2**32 and os.environ.get("PROCESSOR_ARCHITEW6432") is not None


def _run(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True, timeout=60,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise CameraInstallError(f"{' '.join(args)} failed with exit code {result.returncode}")


def dll_dir() -> Path:
    d = find_bundled("vcam", [DLL64])
    if d is None:
        raise CameraInstallError("camera DLLs not found next to the application")
    return d


def install_user() -> CameraStatus:
    """Register the camera for the current user only. No administrator rights required."""
    if sys.platform != "win32":
        raise CameraInstallError("the virtual camera is Windows-only")
    d = dll_dir()
    _run([_regsvr32(64), "/s", "/n", "/i:user", str(d / DLL64)])
    if (d / DLL32).is_file():
        _run([_regsvr32(32), "/s", "/n", "/i:user", str(d / DLL32)])
    st = status()
    if not st.installed:
        raise CameraInstallError("registration reported success but the camera is not visible")
    return st


def uninstall_user() -> CameraStatus:
    if sys.platform != "win32":
        return status()
    st = status()
    for bits, path in ((64, st.x64), (32, st.x86)):
        if path:
            try:
                _run([_regsvr32(bits), "/s", "/u", "/n", "/i:user", path])
            except CameraInstallError:
                pass
    return status()


def install_machine() -> None:
    """Register for all users through a UAC prompt (same as the installer does)."""
    if sys.platform != "win32":
        raise CameraInstallError("the virtual camera is Windows-only")
    d = dll_dir()
    shell32 = ctypes.windll.shell32
    for bits, name in ((64, DLL64), (32, DLL32)):
        if not (d / name).is_file():
            continue
        rc = shell32.ShellExecuteW(None, "runas", _regsvr32(bits), f'/s "{d / name}"', None, 0)
        if rc <= 32:
            raise CameraInstallError("administrator approval was declined" if rc == 5 else f"ShellExecute error {rc}")
