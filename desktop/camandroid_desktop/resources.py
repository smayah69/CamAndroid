"""Locate files shipped with the app: the virtual-camera DLLs and the bundled adb."""

from __future__ import annotations

import filecmp
import os
import shutil
import sys
from pathlib import Path


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "CamAndroid"


def _candidates(name: str) -> list[Path]:
    paths = []
    override = os.environ.get(f"CAMANDROID_{name.upper()}_DIR")
    if override:
        paths.append(Path(override))
    if getattr(sys, "frozen", False):
        paths.append(Path(sys.executable).resolve().parent / name)  # installed next to the exe
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            paths.append(Path(meipass) / name)  # inside the one-file bundle
    return paths


def find_bundled(name: str, required: list[str]) -> Path | None:
    """Return a directory containing all of `required`, copying it out of a temporary
    PyInstaller extraction into %LOCALAPPDATA%\\CamAndroid so it survives this process
    (registered DLLs and a running adb server must not live in a temp folder)."""
    for d in _candidates(name):
        if not all((d / f).is_file() for f in required):
            continue
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and (d == Path(meipass) / name or Path(meipass) in d.parents):
            return _stable_copy(d, name)
        return d
    return None


def _stable_copy(src: Path, name: str) -> Path:
    dst = app_data_dir() / name
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if not f.is_file():
            continue
        target = dst / f.name
        if target.exists() and filecmp.cmp(f, target, shallow=False):
            continue
        try:
            shutil.copy2(f, target)
        except PermissionError:
            # In use (a video app has the old DLL loaded); keep the existing copy.
            if not target.exists():
                raise
    return dst
