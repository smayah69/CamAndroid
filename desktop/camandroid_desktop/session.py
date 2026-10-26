"""Background streaming session: phone -> decode -> virtual camera, with automatic reconnects."""

from __future__ import annotations

import collections
import logging
import queue
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
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
from .video import OutputOptions, Renderer

log = logging.getLogger(__name__)

FATAL_REMOTE_CODES = {"unknown_client", "bad_proof", "version"}


@dataclass
class Target:
    """Where to find the phone: a paired device (Wi-Fi), an explicit address, or USB via adb."""

    device: PairedDevice | None = None
    host: str | None = None
    port: int = protocol.DEFAULT_PORT
    usb: bool = False
    serial: str | None = None
    label: str = ""

    def describe(self) -> str:
        if self.usb:
            return f"{self.label or self.serial or 'phone'} over USB"
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
        preview_interval: float = 0.1,
    ):
        super().__init__(name="camandroid-session", daemon=True)
        self.store, self.target, self.settings, self.output = store, target, settings, output
        self.cb = callbacks or SessionCallbacks()
        self.use_vcam = virtual_camera
        self.preview_interval = preview_interval
        self._stop_event = threading.Event()
        self._conn: PhoneConnection | None = None
        self._pending: queue.Queue[dict] = queue.Queue()
        self.frames = 0
        self.fps = 0.0
        self.codec = ""
        self.delay = 0.0  # seconds the shown video is behind

    # -- control from other threads ----------------------------------------------------------

    def stop(self) -> None:
        self._stop_event.set()
        conn = self._conn
        if conn:
            conn.close()

    def update(self, **changes) -> None:
        """Change phone-side settings (camera, torch, quality, fps) while streaming."""
        for k, v in changes.items():
            setattr(self.settings, k, v)
        self._pending.put(changes)

    # -- thread body -------------------------------------------------------------------------

    def _status(self, text: str) -> None:
        log.info(text)
        self.cb.on_status(text)

    def _error(self, text: str) -> None:
        log.error(text)
        self.cb.on_error(text)

    def run(self) -> None:
        vcam = None
        try:
            if self.use_vcam:
                from .vcam import VirtualCamera

                vcam = VirtualCamera(self.output.width, self.output.height, self.settings.fps)
                self._status("Virtual camera 'CamAndroid' ready")
            backoff = 1.0
            while not self._stop_event.is_set():
                try:
                    self._stream_once(vcam)
                    backoff = 1.0
                except NotPairedError as e:
                    self._error(f"{e}. Pair it first.")
                    return
                except FingerprintMismatchError as e:
                    self._error(str(e))
                    return
                except RemoteError as e:
                    if e.code in FATAL_REMOTE_CODES:
                        self._error(f"Phone refused the connection: {e}. Try pairing again.")
                        return
                    if e.code == "busy":
                        self._status("Phone is busy with another computer; retrying…")
                    else:
                        self._status(f"Phone error: {e}; retrying…")
                except CamAndroidError as e:
                    self._status(f"{e}; retrying…")
                except adb.AdbError as e:
                    self._status(f"USB: {e}")
                except (OSError, ssl.SSLError, ConnectionError, protocol.ProtocolError) as e:
                    if self._stop_event.is_set():
                        break
                    self._status(f"Connection lost ({e.__class__.__name__}); reconnecting in {backoff:.0f}s…")
                if self._stop_event.wait(backoff):
                    break
                backoff = min(backoff * 2, 10.0)
        except Exception as e:  # noqa: BLE001 - surface anything unexpected to the user
            log.exception("Session failed")
            self._error(str(e))
        finally:
            if vcam:
                vcam.close()
            self.cb.on_finished()

    def _stream_once(self, vcam) -> None:
        conn = connect(self.store, self.target)
        self._conn = conn
        renderer = None
        try:
            if self._stop_event.is_set():
                return
            device = conn.authenticate(self.store, self.target.device)
            if not self.target.usb:
                self.store.update_address(device.fingerprint, conn.host, conn.port)
            conn.settimeout(10.0)
            conn.start(self.settings)
            log.info("Phone %s can encode %s; asked for %s %sx%s@%s", device.name, conn.codecs,
                     self.settings.codecs, self.settings.width, self.settings.height, self.settings.fps)
            self._status(f"Connected to {device.name}, waiting for video…")
            renderer = Renderer(self.output)
            reader = _Reader(conn, self._pending, self._stop_event)
            reader.start()
            lag = LagMeter()
            bad_frames = 0
            last_preview = 0.0
            behind_logged = False
            window_start, window_frames, window_shown, window_bytes = time.monotonic(), 0, 0, 0
            while not self._stop_event.is_set():
                batch = reader.take(timeout=0.5)
                if not batch:
                    continue
                for arrival, f in batch:
                    lag.arrived(arrival, f.timestamp_us)
                    window_bytes += len(f.data)
                window_frames += len(batch)
                *older, (_, newest) = batch
                try:
                    # Frames that piled up are decoded but not shown: only the newest one matters.
                    for _, f in older:
                        renderer.render(f, show=False)
                    arr = renderer.render(newest)
                except Exception:  # noqa: BLE001 - one bad frame must not stop the webcam
                    bad_frames += 1
                    if bad_frames == 1:
                        log.exception("Could not render a frame; skipping it")
                    if bad_frames >= 60:
                        raise
                    continue
                bad_frames = 0
                if arr is None:
                    continue
                if vcam:
                    vcam.send(arr)
                self.frames += 1
                window_shown += 1
                now = time.monotonic()
                self.delay = lag.delay(now, newest.timestamp_us)
                if self.delay > 1.0 and not behind_logged:
                    log.warning("Video is %.1f s behind (network or Wi-Fi congestion)", self.delay)
                behind_logged = self.delay > 0.5 if behind_logged else self.delay > 1.0
                if now - window_start >= 1.0:
                    elapsed = now - window_start
                    self.fps = window_frames / elapsed
                    shown = window_shown / elapsed
                    if renderer.codec != self.codec:
                        self.codec = renderer.codec
                        log.info("Receiving %s video", self.codec.upper())
                    mbps = window_bytes * 8 / elapsed / 1e6
                    window_start, window_frames, window_shown, window_bytes = now, 0, 0, 0
                    text = f"Streaming from {device.name} · {self.fps:.0f} fps"
                    if shown < self.fps - 3:
                        text += f" (shows {shown:.0f})"
                    text += f" · {self.codec.upper()} {mbps:.1f} Mbit/s"
                    if self.delay >= 0.3:
                        text += f" · {self.delay:.1f} s behind"
                    self._status(text)
                if self.cb.on_preview and now - last_preview >= self.preview_interval:
                    last_preview = now
                    self.cb.on_preview(Image.fromarray(np.ascontiguousarray(arr)))
        except TimeoutError as e:
            raise ConnectionError("no video from phone for 10 s") from e
        finally:
            self._conn = None
            conn.close()
            if renderer:
                renderer.close()


class _Reader(threading.Thread):
    """Reads the phone's messages as fast as they arrive, so video never waits in the OS receive
    buffer while the session thread is busy. It also sends the pending setting changes: a TLS
    connection must not be read and written from two threads at once."""

    def __init__(self, conn: PhoneConnection, pending: queue.Queue, stop: threading.Event):
        super().__init__(name="camandroid-reader", daemon=True)
        self.conn, self.pending, self.stop = conn, pending, stop
        self._frames: list[tuple[float, protocol.Frame]] = []
        self._cond = threading.Condition()
        self._error: BaseException | None = None
        self._done = False

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                self._flush_pending()
                msg = self.conn.read()
                if isinstance(msg, protocol.Frame):
                    with self._cond:
                        self._frames.append((time.monotonic(), msg))
                        self._cond.notify()
                elif msg.get("op") in ("started", "updated"):
                    log.info("Phone %s: %s", msg.get("op"), {k: v for k, v in msg.items() if k != "op"})
        except BaseException as e:  # noqa: BLE001 - handed to the session thread
            self._error = e
        finally:
            with self._cond:
                self._done = True
                self._cond.notify()

    def _flush_pending(self) -> None:
        while True:
            try:
                changes = self.pending.get_nowait()
            except queue.Empty:
                return
            self.conn.update(**changes)

    def take(self, timeout: float) -> list[tuple[float, protocol.Frame]]:
        """Everything received since the last call (arrival time, frame); raises when the reader stopped."""
        with self._cond:
            if not self._frames and not self._done:
                self._cond.wait(timeout)
            batch, self._frames = self._frames, []
            if batch or not self._done:
                return batch
        if self._error is not None:
            raise self._error
        raise ConnectionError("connection closed by phone")


class LagMeter:
    """How far behind real time the shown video is, beyond the usual capture-to-screen delay.

    Phone timestamps and this computer's clock are unrelated, so the smallest difference seen
    recently (the fastest a frame ever got here) is taken as "on time".
    """

    WINDOW_S = 60.0

    def __init__(self):
        self._buckets: collections.deque[tuple[int, float]] = collections.deque()  # (second, min offset)
        self._last_ts = None

    def arrived(self, arrival: float, timestamp_us: int) -> None:
        ts = timestamp_us / 1e6
        if self._last_ts is not None and not (0 <= ts - self._last_ts < 5):
            self._buckets.clear()  # camera restarted or switched: new time base
        self._last_ts = ts
        offset = arrival - ts
        second = int(arrival)
        if self._buckets and self._buckets[-1][0] == second:
            self._buckets[-1] = (second, min(self._buckets[-1][1], offset))
        else:
            self._buckets.append((second, offset))
        while self._buckets and self._buckets[0][0] < second - self.WINDOW_S:
            self._buckets.popleft()

    def delay(self, now: float, timestamp_us: int) -> float:
        if not self._buckets:
            return 0.0
        return max(0.0, now - timestamp_us / 1e6 - min(o for _, o in self._buckets))
