"""Persistent storage for this computer's identity and the phones it has paired with."""

from __future__ import annotations

import json
import os
import platform
import socket
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


def config_dir() -> Path:
    override = os.environ.get("CAMANDROID_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "CamAndroid"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CamAndroid"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "camandroid"


@dataclass
class PairedDevice:
    fingerprint: str
    name: str
    token: str
    host: str = ""
    port: int = 0
    paired_at: float = 0.0

    @property
    def device_id(self) -> str:
        return self.fingerprint[:16]


class DeviceStore:
    """JSON file holding the client id and paired devices. Tokens are secrets: file is 0600."""

    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "devices.json"
        self.client_id = ""
        self.client_name = socket.gethostname() or platform.node() or "Computer"
        self.devices: dict[str, PairedDevice] = {}
        self.load()

    def load(self) -> None:
        data: dict = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
            except (OSError, ValueError):
                data = {}
        self.client_id = data.get("client_id") or uuid.uuid4().hex
        self.client_name = data.get("client_name") or self.client_name
        self.devices = {}
        for d in data.get("devices", []):
            try:
                dev = PairedDevice(**d)
            except TypeError:
                continue
            self.devices[dev.fingerprint] = dev
        if not data.get("client_id"):
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "client_id": self.client_id,
            "client_name": self.client_name,
            "devices": [asdict(d) for d in self.devices.values()],
        }
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    def get(self, fingerprint: str) -> PairedDevice | None:
        return self.devices.get(fingerprint)

    def find(self, query: str) -> PairedDevice | None:
        """Look up a device by device id / fingerprint prefix or by (case-insensitive) name."""
        q = query.lower()
        for d in self.devices.values():
            if d.fingerprint.startswith(q) or d.name.lower() == q:
                return d
        return None

    def add(self, device: PairedDevice) -> None:
        device.paired_at = device.paired_at or time.time()
        self.devices[device.fingerprint] = device
        self.save()

    def update_address(self, fingerprint: str, host: str, port: int) -> None:
        dev = self.devices.get(fingerprint)
        if dev and (dev.host, dev.port) != (host, port):
            dev.host, dev.port = host, port
            self.save()

    def remove(self, fingerprint: str) -> bool:
        if self.devices.pop(fingerprint, None) is None:
            return False
        self.save()
        return True
