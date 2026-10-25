"""USB connections: forward a local TCP port to the phone with adb."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .protocol import DEFAULT_PORT

LOCAL_USB_PORT = 47470


class AdbError(Exception):
    pass


def find_adb() -> str:
    exe = "adb.exe" if sys.platform == "win32" else "adb"
    found = shutil.which("adb")
    if found:
        return found
    candidates = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")]
    if sys.platform == "win32":
        candidates.append(str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk"))
    elif sys.platform == "darwin":
        candidates.append(str(Path.home() / "Library" / "Android" / "sdk"))
    else:
        candidates.append(str(Path.home() / "Android" / "Sdk"))
    for root in filter(None, candidates):
        path = Path(root) / "platform-tools" / exe
        if path.exists():
            return str(path)
    raise AdbError(
        "adb not found. Install Android platform-tools "
        "(https://developer.android.com/tools/releases/platform-tools) and add it to PATH."
    )


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run([find_adb(), *args], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired as e:
        raise AdbError("adb timed out") from e
    if result.returncode != 0:
        raise AdbError((result.stderr or result.stdout).strip() or f"adb {' '.join(args)} failed")
    return result.stdout


def devices() -> list[str]:
    """Serials of connected, authorized devices."""
    lines = _run(["devices"]).splitlines()[1:]
    return [line.split("\t")[0] for line in lines if line.strip().endswith("\tdevice")]


def forward(serial: str | None = None, local_port: int = LOCAL_USB_PORT, remote_port: int = DEFAULT_PORT) -> int:
    """Forward localhost:local_port to the phone's server port. Returns the local port."""
    found = devices()
    if not found:
        raise AdbError(
            "No phone found over USB. Enable Developer options > USB debugging on the phone, "
            "connect the cable and accept the 'Allow USB debugging' prompt."
        )
    if serial is None:
        if len(found) > 1:
            raise AdbError(f"Several devices connected ({', '.join(found)}); choose one with --serial.")
        serial = found[0]
    _run(["-s", serial, "forward", f"tcp:{local_port}", f"tcp:{remote_port}"])
    return local_port
