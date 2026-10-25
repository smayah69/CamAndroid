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
        import av

        av.CodecContext.create("h264", "r")
    except Exception:  # noqa: BLE001 - missing or broken FFmpeg: JPEG still works
        return ("jpeg",)
    return ("h264", "jpeg")


class H264Decoder:
    """Low-latency FFmpeg H.264 decoder: frames come out as soon as they go in."""

    def __init__(self):
        import av
        from av.codec.context import Flags

        self._av = av
        self.ctx = av.CodecContext.create("h264", "r")
        self.ctx.flags |= int(Flags.low_delay)
        self.ctx.thread_type = "SLICE"  # frame threading would add a frame of delay per thread
        self._synced = False

    def decode(self, frame: Frame):
        """Returns the decoded av.VideoFrame, or None while waiting for a key frame."""
        if not self._synced and not frame.flags & FLAG_KEY_FRAME:
            return None
        try:
            frames = self.ctx.decode(self._av.Packet(frame.data))
        except self._av.error.FFmpegError as e:
            log.warning("H.264 decode error, waiting for the next key frame: %s", e)
            self._synced = False
            return None
        self._synced = True
        return frames[-1] if frames else None


class Renderer:
    """Decodes frames and rotates, mirrors and scales them to the output size.

    Scaling happens during decoding (libjpeg draft mode, or swscale for H.264) on the unrotated
    image, so each frame is touched as few times as possible.
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
            decoded = self._h264.decode(frame)
            if decoded is None:
                return None
            w, h = _scaled_size((decoded.width, decoded.height), swap, opts)
            arr = decoded.reformat(width=w, height=h, format="rgb24").to_ndarray()
        return compose(arr, rotation, opts)


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
