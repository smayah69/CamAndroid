"""Virtual webcam output via pyvirtualcam (OBS Virtual Camera, Unity Capture or v4l2loopback)."""

from __future__ import annotations

import sys

import numpy as np


class VirtualCameraError(Exception):
    pass


SETUP_HINT = {
    "win32": "Install OBS Studio (it ships the 'OBS Virtual Camera' driver), or Unity Capture.",
    "darwin": "Install OBS Studio 30+ and start its Virtual Camera once so macOS approves the extension.",
    "linux": (
        "Load v4l2loopback: sudo modprobe v4l2loopback devices=1 exclusive_caps=1 "
        'card_label="CamAndroid"'
    ),
}


class VirtualCamera:
    def __init__(self, width: int, height: int, fps: int, backend: str | None = None, device: str | None = None):
        try:
            import pyvirtualcam
        except ImportError as e:
            raise VirtualCameraError("pyvirtualcam is not installed (pip install pyvirtualcam)") from e
        try:
            self._cam = pyvirtualcam.Camera(
                width=width, height=height, fps=fps,
                fmt=pyvirtualcam.PixelFormat.RGB, backend=backend, device=device,
            )
        except RuntimeError as e:
            hint = SETUP_HINT.get(sys.platform, "")
            raise VirtualCameraError(f"Could not open a virtual camera: {e}\n{hint}") from e
        self.width, self.height = width, height

    @property
    def device(self) -> str:
        return self._cam.device

    @property
    def backend(self) -> str:
        return self._cam.backend

    def send(self, frame: np.ndarray) -> None:
        self._cam.send(frame)

    def close(self) -> None:
        self._cam.close()
