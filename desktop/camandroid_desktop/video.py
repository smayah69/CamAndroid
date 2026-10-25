"""Turn phone JPEG frames into fixed-size RGB images for the virtual camera."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .protocol import Frame

_TRANSPOSE_CLOCKWISE = {
    90: Image.Transpose.ROTATE_270,  # PIL rotates counter-clockwise
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


@dataclass
class OutputOptions:
    width: int = 1280
    height: int = 720
    rotate: int = 0  # extra clockwise rotation on top of the phone's own orientation
    mirror: bool = False
    fill: bool = False  # crop to fill instead of letterboxing


def decode(frame: Frame) -> Image.Image:
    return Image.open(io.BytesIO(frame.jpeg))


def render(frame: Frame, opts: OutputOptions) -> Image.Image:
    img = decode(frame)
    rotation = (frame.rotation + opts.rotate) % 360
    # Let libjpeg downscale during decode when the phone sends more pixels than we need.
    target = (opts.height, opts.width) if rotation in (90, 270) else (opts.width, opts.height)
    img.draft("RGB", target)
    img = img.convert("RGB")
    if rotation in _TRANSPOSE_CLOCKWISE:
        img = img.transpose(_TRANSPOSE_CLOCKWISE[rotation])
    if opts.mirror:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return fit(img, opts.width, opts.height, opts.fill)


def fit(img: Image.Image, width: int, height: int, fill: bool = False) -> Image.Image:
    if img.size == (width, height):
        return img
    w, h = img.size
    scale = (max if fill else min)(width / w, height / h)
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    resized = img.resize(new_size, Image.Resampling.BILINEAR)
    if fill:
        left = (new_size[0] - width) // 2
        top = (new_size[1] - height) // 2
        return resized.crop((left, top, left + width, top + height))
    canvas = Image.new("RGB", (width, height))
    canvas.paste(resized, ((width - new_size[0]) // 2, (height - new_size[1]) // 2))
    return canvas


def to_array(img: Image.Image) -> np.ndarray:
    return np.asarray(img, dtype=np.uint8)
