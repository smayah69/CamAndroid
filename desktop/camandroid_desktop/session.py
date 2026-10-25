"""Background streaming session: phone -> decode -> virtual camera, with automatic reconnects."""

from __future__ import annotations

import queue
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

from . import adb, protocol
from .client import (
    CamAndroidError,
    FingerprintMismatchError,
    NotPairedError,
    PhoneConnection,
    RemoteError,
    StreamSettings,
)
from .store import DeviceStore, PairedDevice
from .video import OutputOptions, render, to_array

FATAL_REMOTE_CODES = {"unknown_client", "bad_proof", "version"}


@dataclass
class Target:
    """Where to find the phone: a paired device (Wi-Fi), an explicit address, or USB via adb."""

    device: PairedDevice | None = None
    host: str | None = None
    port: int = protocol.DEFAULT_PORT
    usb: bool = False
    serial: str | None = None

    def describe(self) -> str:
        if self.usb:
            return "USB"
        if self.device:
            return self.device.name
        return f"{self.host}:{self.port}"


@dataclass
class SessionCallbacks:
    on_status: Callable[[str], None] = lambda s: None
    on_error: Callable[[str], None] = lambda s: None
    on_preview: Callable[[Image.Image], None] | None = None
    on_finished: Callable[[], None] = lambda: None


def connect(store: DeviceStore, target: Target, timeout: float = 8.0) -> PhoneConnection:
    """Open a connection to the target; for paired Wi-Fi devices fall back to mDNS lookup."""
    if target.usb:
        port = adb.forward(target.serial, remote_port=target.port)
        return PhoneConnection("127.0.0.1", port, timeout)
    if target.host:
        return PhoneConnection(target.host, target.port, timeout)
    dev = target.device
    if dev is None:
        raise CamAndroidError("no phone selected")
    last_error: Exception | None = None
    if dev.host:
        try:
            return PhoneConnection(dev.host, dev.port or protocol.DEFAULT_PORT, timeout)
        except OSError as e:
            last_error = e
    try:
        from .discovery import resolve

        found = resolve(dev.device_id)
    except ImportError:
        found = None
    if found:
        conn = PhoneConnection(found.host, found.port, timeout)
        store.update_address(dev.fingerprint, found.host, found.port)
        return conn
    raise CamAndroidError(
        f"Could not reach {dev.name}. Make sure the app is running and both devices share a network"
        + (f" ({last_error})" if last_error else "")
    )


class StreamSession(threading.Thread):
    def __init__(
        self,
        store: DeviceStore,
        target: Target,
        settings: StreamSettings,
        output: OutputOptions,
        callbacks: SessionCallbacks | None = None,
        virtual_camera: bool = True,
        backend: str | None = None,
        device: str | None = None,
        preview_interval: float = 0.1,
    ):
        super().__init__(name="camandroid-session", daemon=True)
        self.store, self.target, self.settings, self.output = store, target, settings, output
        self.cb = callbacks or SessionCallbacks()
        self.use_vcam, self.backend, self.vcam_device = virtual_camera, backend, device
        self.preview_interval = preview_interval
        self._stop = threading.Event()
        self._conn: PhoneConnection | None = None
        self._pending: queue.Queue[dict] = queue.Queue()
        self.frames = 0
        self.fps = 0.0

    # -- control from other threads ----------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()
        conn = self._conn
        if conn:
            conn.close()

    def update(self, **changes) -> None:
        """Change phone-side settings (camera, torch, quality, fps) while streaming."""
        for k, v in changes.items():
            setattr(self.settings, k, v)
        self._pending.put(changes)

    # -- thread body -------------------------------------------------------------------------

    def run(self) -> None:
        vcam = None
        try:
            if self.use_vcam:
                from .vcam import VirtualCamera

                vcam = VirtualCamera(self.output.width, self.output.height, self.settings.fps,
                                     self.backend, self.vcam_device)
                self.cb.on_status(f"Virtual camera ready: {vcam.device}")
            backoff = 1.0
            while not self._stop.is_set():
                try:
                    self._stream_once(vcam)
                    backoff = 1.0
                except NotPairedError as e:
                    self.cb.on_error(f"{e}. Pair it first.")
                    return
                except FingerprintMismatchError as e:
                    self.cb.on_error(str(e))
                    return
                except RemoteError as e:
                    if e.code in FATAL_REMOTE_CODES:
                        self.cb.on_error(f"Phone refused the connection: {e}. Try pairing again.")
                        return
                    if e.code == "busy":
                        self.cb.on_status("Phone is busy with another computer; retrying…")
                    else:
                        self.cb.on_status(f"Phone error: {e}; retrying…")
                except CamAndroidError as e:
                    self.cb.on_status(f"{e}; retrying…")
                except adb.AdbError as e:
                    self.cb.on_status(f"USB: {e}")
                except (OSError, ssl.SSLError, ConnectionError, protocol.ProtocolError) as e:
                    if self._stop.is_set():
                        break
                    self.cb.on_status(f"Connection lost ({e.__class__.__name__}); reconnecting in {backoff:.0f}s…")
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 10.0)
        except Exception as e:  # noqa: BLE001 - surface anything unexpected to the user
            self.cb.on_error(str(e))
        finally:
            if vcam:
                vcam.close()
            self.cb.on_finished()

    def _stream_once(self, vcam) -> None:
        conn = connect(self.store, self.target)
        self._conn = conn
        try:
            if self._stop.is_set():
                return
            device = conn.authenticate(self.store, self.target.device)
            if not self.target.usb:
                self.store.update_address(device.fingerprint, conn.host, conn.port)
            conn.settimeout(10.0)
            conn.start(self.settings)
            self.cb.on_status(f"Connected to {device.name}, waiting for video…")
            last_preview = 0.0
            window_start, window_frames = time.monotonic(), 0
            while not self._stop.is_set():
                self._flush_pending(conn)
                msg = conn.read()
                if not isinstance(msg, protocol.Frame):
                    continue
                img = render(msg, self.output)
                if vcam:
                    vcam.send(to_array(img))
                self.frames += 1
                window_frames += 1
                now = time.monotonic()
                if now - window_start >= 1.0:
                    self.fps = window_frames / (now - window_start)
                    window_start, window_frames = now, 0
                    self.cb.on_status(f"Streaming from {device.name} · {self.fps:.0f} fps")
                if self.cb.on_preview and now - last_preview >= self.preview_interval:
                    last_preview = now
                    self.cb.on_preview(img)
        except TimeoutError as e:
            raise ConnectionError("no video from phone for 10 s") from e
        finally:
            self._conn = None
            conn.close()

    def _flush_pending(self, conn: PhoneConnection) -> None:
        while True:
            try:
                changes = self._pending.get_nowait()
            except queue.Empty:
                return
            conn.update(**changes)
