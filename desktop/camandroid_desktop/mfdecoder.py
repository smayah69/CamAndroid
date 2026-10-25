"""H.264 decoding with Windows' built-in Media Foundation decoder (camandroid-h264.dll).

Windows ships the decoder (hardware accelerated where the GPU supports it), so the app does not
have to bundle FFmpeg. The DLL is built from windows/vcam/src/h264dec.cpp.
"""

from __future__ import annotations

import ctypes
import sys

import numpy as np

from .resources import find_bundled

DLL = "camandroid-h264.dll"


class DecoderUnavailable(Exception):
    pass


_lib = None


def _load():
    global _lib
    if _lib is None:
        if sys.platform != "win32":
            raise DecoderUnavailable("Media Foundation is Windows-only")
        folder = find_bundled("codec", [DLL])
        if folder is None:
            raise DecoderUnavailable(f"{DLL} not found next to the application")
        lib = ctypes.CDLL(str(folder / DLL))  # ctypes releases the GIL during calls
        lib.cah_create.restype = ctypes.c_void_p
        lib.cah_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.cah_size.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        lib.cah_to_rgb.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.cah_destroy.argtypes = [ctypes.c_void_p]
        _lib = lib
    return _lib


class MFDecoder:
    def __init__(self):
        self._handle = None
        self._lib = _load()
        self._handle = self._lib.cah_create()
        if not self._handle:
            raise DecoderUnavailable("Windows has no H.264 decoder (install the Media Feature Pack on N editions)")

    def decode(self, data: bytes) -> bool:
        """Feeds one access unit. True when a new picture is ready."""
        rc = self._lib.cah_decode(self._handle, data, len(data))
        if rc < 0:
            raise ValueError(f"H.264 decode failed (HRESULT 0x{rc & 0xFFFFFFFF:08X})")
        return rc == 1

    @property
    def size(self) -> tuple[int, int]:
        w, h = ctypes.c_int(), ctypes.c_int()
        self._lib.cah_size(self._handle, ctypes.byref(w), ctypes.byref(h))
        return w.value, h.value

    def to_rgb(self, width: int, height: int) -> np.ndarray:
        """The latest picture as an RGB array, scaled to width x height."""
        out = np.empty((height, width, 3), dtype=np.uint8)
        if not self._lib.cah_to_rgb(self._handle, out.ctypes.data, width, height, width * 3):
            raise ValueError("no picture decoded yet")
        return out

    def close(self) -> None:
        if self._handle:
            self._lib.cah_destroy(self._handle)
            self._handle = None

    def __del__(self):
        self.close()
