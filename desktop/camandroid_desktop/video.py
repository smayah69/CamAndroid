"""Turn phone frames (H.264 or JPEG) into fixed-size RGB images for the virtual camera."""

from __future__ import annotations

import functools
import io
import logging
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .protocol import FLAG_KEY_FRAME, Frame

log = logging.getLogger(__name__)

JPEG_MAGIC = b"\xff\xd8"


@dataclass
class OutputOptions:
    width: int = 1280
    height: int = 720
    rotate: int = 0  # extra clockwise rotation on top of the phone's own orientation
    mirror: bool = True
    fill: bool = False  # crop to fill instead of letterboxing


def available_codecs() -> list[str]:
    """Codecs this computer can decode, most preferred first (sent to the phone)."""
    return list(_codecs())


@functools.cache
def _codecs() -> tuple[str, ...]:
    try:
        H264Decoder().close()
    except Exception as e:  # noqa: BLE001 - no usable decoder: JPEG still works
        log.warning("H.264 decoding unavailable, the phone will send JPEG: %s", e)
        return ("jpeg",)
    return ("h264", "jpeg")


class _MFBackend:
    """Windows' Media Foundation decoder (what the released app uses)."""

    name = "Media Foundation"

    def __init__(self):
        from .mfdecoder import MFDecoder

        self._dec = MFDecoder()

    def decode(self, data: bytes) -> tuple[int, int] | None:
        return self._dec.size if self._dec.decode(data) else None

    def picture(self, width: int, height: int) -> np.ndarray:
        return self._dec.to_rgb(width, height)

    def close(self) -> None:
        self._dec.close()


class _FFmpegBackend:
    """FFmpeg through PyAV, for development on other systems (pip install av)."""

    name = "FFmpeg"

    def __init__(self):
        import av
        from av.codec.context import Flags

        self._av = av
        self.ctx = av.CodecContext.create("h264", "r")
        self.ctx.flags |= int(Flags.low_delay)  # no reordering delay: frames come out as they go in
        self.ctx.thread_type = "SLICE"  # frame threading would add a frame of delay per thread
        self._frame = None

    def decode(self, data: bytes) -> tuple[int, int] | None:
        frames = self.ctx.decode(self._av.Packet(data))
        if not frames:
            return None
        self._frame = frames[-1]
        return self._frame.width, self._frame.height

    def picture(self, width: int, height: int) -> np.ndarray:
        return self._frame.reformat(width=width, height=height, format="rgb24").to_ndarray()

    def close(self) -> None:
        self._frame = None


class H264Decoder:
    """Low-latency H.264 decoder that skips ahead to the next key frame after an error."""

    def __init__(self):
        errors = []
        for backend in (_MFBackend, _FFmpegBackend):
            try:
                self._backend = backend()
                self.name = backend.name
                break
            except Exception as e:  # noqa: BLE001 - try the next one
                errors.append(f"{backend.name}: {e}")
        else:
            raise RuntimeError("; ".join(errors))
        self._synced = False

    def decode(self, frame: Frame) -> tuple[int, int] | None:
        """Returns the picture size when a new picture is ready, or None (waiting for more data)."""
        if not self._synced and not frame.flags & FLAG_KEY_FRAME:
            return None
        try:
            size = self._backend.decode(frame.data)
        except Exception as e:  # noqa: BLE001 - corrupt data must not end the stream
            log.warning("H.264 decode error, waiting for the next key frame: %s", e)
            self._synced = False
            return None
        self._synced = True
        return size

    def picture(self, width: int, height: int) -> np.ndarray:
        """The latest picture, scaled to width x height while converting to RGB."""
        return self._backend.picture(width, height)

    def close(self) -> None:
        self._backend.close()


class Renderer:
    """Decodes frames and rotates, mirrors and scales them to the output size.

    Scaling happens during decoding (libjpeg draft mode) or the YUV to RGB conversion (H.264) on
    the unrotated image, so each frame is touched as few times as possible.
    """

    def __init__(self, opts: OutputOptions):
        self.opts = opts
        self._h264: H264Decoder | None = None
        self.codec = ""

    def render(self, frame: Frame) -> np.ndarray | None:
        opts = self.opts
        rotation = (frame.rotation + opts.rotate) % 360
        swap = rotation in (90, 270)
        if frame.data[:2] == JPEG_MAGIC:
            self.codec = "jpeg"
            img = Image.open(io.BytesIO(frame.data))
            size = _scaled_size(img.size, swap, opts)
            img.draft("RGB", size)  # libjpeg downscales by 1/2, 1/4 or 1/8 while decoding
            img = img.convert("RGB")
            if img.size != size:
                img = img.resize(size, Image.Resampling.BILINEAR)
            arr = np.asarray(img)
        else:
            self.codec = "h264"
            if self._h264 is None:
                self._h264 = H264Decoder()
                log.info("Decoding H.264 with %s", self._h264.name)
            size = self._h264.decode(frame)
            if size is None:
                return None
            arr = self._h264.picture(*_scaled_size(size, swap, opts))
        return compose(arr, rotation, opts)

    @property
    def decoder_name(self) -> str:
        return self._h264.name if self._h264 else ""

    def close(self) -> None:
        if self._h264:
            self._h264.close()
            self._h264 = None


def _scaled_size(size: tuple[int, int], swap: bool, opts: OutputOptions) -> tuple[int, int]:
    """Size to scale the unrotated image to, so that after rotation it fits (or fills) the output."""
    w, h = size
    rw, rh = (h, w) if swap else (w, h)
    scale = (max if opts.fill else min)(opts.width / rw, opts.height / rh)
    sw, sh = max(2, round(rw * scale)), max(2, round(rh * scale))
    if not opts.fill:
        sw, sh = min(sw, opts.width), min(sh, opts.height)
    return (sh, sw) if swap else (sw, sh)


def compose(arr: np.ndarray, rotation: int, opts: OutputOptions) -> np.ndarray:
    """Rotates (clockwise), mirrors and letterboxes or crops a scaled RGB image to the output size."""
    if rotation:
        arr = np.rot90(arr, k=-(rotation // 90))
    if opts.mirror:
        arr = arr[:, ::-1]
    h, w = arr.shape[:2]
    if (w, h) == (opts.width, opts.height):
        return arr
    if opts.fill:
        top, left = max(0, (h - opts.height) // 2), max(0, (w - opts.width) // 2)
        arr = arr[top : top + opts.height, left : left + opts.width]
        h, w = arr.shape[:2]
        if (w, h) == (opts.width, opts.height):
            return arr
    canvas = np.zeros((opts.height, opts.width, 3), dtype=np.uint8)
    top, left = (opts.height - h) // 2, (opts.width - w) // 2
    canvas[top : top + h, left : left + w] = arr
    return canvas


def render(frame: Frame, opts: OutputOptions) -> Image.Image:
    """One-shot render of a single frame (JPEG, or an H.264 key frame) to a PIL image."""
    arr = Renderer(opts).render(frame)
    if arr is None:
        raise ValueError("frame could not be decoded on its own")
    return Image.fromarray(np.ascontiguousarray(arr))


def to_array(img: Image.Image) -> np.ndarray:
    return np.asarray(img, dtype=np.uint8)
