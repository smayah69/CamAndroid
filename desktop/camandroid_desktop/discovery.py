"""Find phones on the local network via mDNS / DNS-SD (the app advertises _camandroid._tcp)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .protocol import SERVICE_TYPE


@dataclass(frozen=True)
class DiscoveredPhone:
    name: str
    host: str
    port: int
    device_id: str

    @property
    def label(self) -> str:
        return f"{self.name} ({self.host}:{self.port})"


class PhoneBrowser:
    """Continuously tracks phones on the network; calls on_change from a background thread."""

    def __init__(self, on_change: Callable[[list[DiscoveredPhone]], None] | None = None):
        from zeroconf import ServiceBrowser, Zeroconf

        self._on_change = on_change
        self._phones: dict[str, DiscoveredPhone] = {}
        self._lock = threading.Lock()
        self._zc = Zeroconf()
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, handlers=[self._handle])

    def _handle(self, zeroconf, service_type: str, name: str, state_change) -> None:
        from zeroconf import ServiceStateChange

        if state_change is ServiceStateChange.Removed:
            with self._lock:
                changed = self._phones.pop(name, None) is not None
        else:
            info = zeroconf.get_service_info(service_type, name, timeout=3000)
            if info is None:
                return
            addresses = [a for a in info.parsed_addresses() if ":" not in a] or info.parsed_addresses()
            if not addresses:
                return
            props = {k.decode(errors="ignore"): (v or b"").decode(errors="ignore") for k, v in info.properties.items()}
            phone = DiscoveredPhone(
                name=name.removesuffix("." + SERVICE_TYPE),
                host=addresses[0],
                port=info.port or 0,
                device_id=props.get("id", ""),
            )
            with self._lock:
                changed = self._phones.get(name) != phone
                self._phones[name] = phone
        if changed and self._on_change:
            self._on_change(self.phones())

    def phones(self) -> list[DiscoveredPhone]:
        with self._lock:
            return sorted(self._phones.values(), key=lambda d: d.name)

    def find(self, device_id: str) -> DiscoveredPhone | None:
        return next((d for d in self.phones() if d.device_id and d.device_id == device_id), None)

    def close(self) -> None:
        self._browser.cancel()
        self._zc.close()


def discover(timeout: float = 3.0) -> list[DiscoveredPhone]:
    browser = PhoneBrowser()
    try:
        time.sleep(timeout)
        return browser.phones()
    finally:
        browser.close()


def resolve(device_id: str, timeout: float = 4.0) -> DiscoveredPhone | None:
    """Find the current address of a paired phone by its device id."""
    browser = PhoneBrowser()
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = browser.find(device_id)
            if found:
                return found
            time.sleep(0.2)
        return None
    finally:
        browser.close()
