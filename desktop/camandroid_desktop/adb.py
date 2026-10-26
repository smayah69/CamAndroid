"""USB connections: forward a local TCP port to the phone with adb."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .protocol import DEFAULT_PORT
from .resources import find_bundled

LOCAL_USB_PORT = 47470
# Release and debug builds of the Android app; the action makes it start its server right away.
APP_PACKAGES = ("com.camandroid.app", "com.camandroid.app.debug")
START_SERVER_ACTION = "com.camandroid.app.action.START_SERVER"

log = logging.getLogger(__name__)


class AdbError(Exception):
    pass


def find_adb() -> str:
    exe = "adb.exe" if sys.platform == "win32" else "adb"
    bundled = find_bundled("adb", [exe, "AdbWinApi.dll"] if sys.platform == "win32" else [exe])
    if bundled:
        return str(bundled / exe)
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
    raise AdbError("adb not found. It ships with the CamAndroid installer; reinstall the app.")


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run([find_adb(), *args], capture_output=True, text=True, timeout=15,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise AdbError("adb timed out") from e
    if result.returncode != 0:
        raise AdbError((result.stderr or result.stdout).strip() or f"adb {' '.join(args)} failed")
    return result.stdout


def device_states() -> dict[str, str]:
    """Serial -> state ('device', 'unauthorized', 'offline', ...)."""
    out = {}
    for line in _run(["devices"]).splitlines()[1:]:
        parts = line.strip().split("\t")
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


@dataclass(frozen=True)
class UsbPhone:
    serial: str
    state: str  # "device" when ready, "unauthorized", "offline", ...
    model: str

    @property
    def ready(self) -> bool:
        return self.state == "device"

    @property
    def wireless(self) -> bool:
        """adb over Wi-Fi ("Wireless debugging") rather than a cable."""
        return ":" in self.serial or "._adb-tls-connect." in self.serial


def list_phones() -> list[UsbPhone]:
    """Phones adb can see, with their model names (``adb devices -l``)."""
    phones = []
    for line in _run(["devices", "-l"]).splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        props = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        model = props.get("model", "").replace("_", " ") or parts[0]
        phones.append(UsbPhone(parts[0], parts[1], model))
    return phones


def devices() -> list[str]:
    """Serials of connected, authorized devices."""
    return [s for s, state in device_states().items() if state == "device"]


def start_app(serial: str | None = None) -> str:
    """Wakes the phone and opens CamAndroid with its server running. Returns the package started."""
    serial = serial or _single_device()
    _run(["-s", serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
    for package in APP_PACKAGES:
        out = _run(["-s", serial, "shell", "am", "start", "-a", START_SERVER_ACTION,
                    "-n", f"{package}/com.camandroid.app.MainActivity"])
        if "Error" not in out:
            log.info("adb: started %s on %s", package, serial)
            return package
    raise AdbError("CamAndroid is not installed on the phone. Install the APK first.")


def _single_device() -> str:
    found = devices()
    if len(found) != 1:
        raise AdbError("Connect exactly one phone by USB, or choose one with --serial.")
    return found[0]


def forward(serial: str | None = None, local_port: int = LOCAL_USB_PORT, remote_port: int = DEFAULT_PORT) -> int:
    """Forward localhost:local_port to the phone's server port. Returns the local port."""
    states = device_states()
    found = [s for s, state in states.items() if state == "device"]
    if not found and "unauthorized" in states.values():
        raise AdbError("Unlock the phone and accept the 'Allow USB debugging?' prompt (tick 'Always allow').")
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
    log.info("adb: forwarding localhost:%d to %s port %d", local_port, serial, remote_port)
    return local_port
