"""Output to the CamAndroid virtual camera (our own DirectShow camera DLL, no OBS needed).

Frames go through a named shared-memory section that the camera DLL reads from inside the
video app. The layout is defined in windows/vcam/src/shared_frame.h; keep the two in sync.
"""

from __future__ import annotations

import ctypes
import struct
import sys
import time

import numpy as np

MAPPING_NAME = "Local\\CamAndroidVCam"
MAGIC = 0x43564143
VERSION = 1
MAX_WIDTH, MAX_HEIGHT = 3840, 2160
HEADER = struct.Struct("<IIIIIIQQ24x")  # magic, version, width, height, seq, fps, frame_index, timestamp_ms
HEADER_SIZE = 64
MAP_SIZE = HEADER_SIZE + MAX_WIDTH * MAX_HEIGHT * 3
SEQ_OFFSET = 16

# Owner and SYSTEM get full access; the low-integrity label lets sandboxed (low IL) capture
# processes of the same user read frames.
_SDDL = "D:(A;;GA;;;OW)(A;;GA;;;SY)S:(ML;;NW;;;LW)"


class VirtualCameraError(Exception):
    pass


class VirtualCamera:
    """Publishes RGB frames to the CamAndroid camera. One writer per Windows session."""

    def __init__(self, width: int, height: int, fps: int):
        if sys.platform != "win32":
            raise VirtualCameraError("The CamAndroid virtual camera is only available on Windows.")
        from . import camera_install

        if not camera_install.status().installed:
            raise VirtualCameraError(
                "The CamAndroid camera is not installed. Click 'Install camera' in the app "
                "or run 'camandroid camera install'."
            )
        self.width, self.height, self.fps = width, height, fps
        self._handle, self._addr = _create_mapping()
        self._buf = (ctypes.c_ubyte * MAP_SIZE).from_address(self._addr)
        self._view = memoryview(self._buf).cast("B")
        self._pixels = np.frombuffer(self._buf, dtype=np.uint8, count=MAP_SIZE - HEADER_SIZE, offset=HEADER_SIZE)
        self._seq = struct.unpack_from("<I", self._view, SEQ_OFFSET)[0] & ~1
        self._index = 0
        self.device = "CamAndroid"

    def send(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        if frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError("frame must be an RGB uint8 array of shape (h, w, 3)")
        if w > MAX_WIDTH or h > MAX_HEIGHT:
            raise ValueError(f"frame larger than {MAX_WIDTH}x{MAX_HEIGHT}")
        n = w * h * 3
        self._seq += 1  # odd: readers skip the frame while we write
        struct.pack_into("<I", self._view, SEQ_OFFSET, self._seq)
        # One strided copy straight into shared memory, whatever the frame's memory layout.
        self._pixels[:n].reshape(h, w, 3)[...] = frame
        self._index += 1
        HEADER.pack_into(self._view, 0, MAGIC, VERSION, w, h, self._seq, self.fps, self._index,
                         int(time.time() * 1000))
        self._seq += 1  # even: stable
        struct.pack_into("<I", self._view, SEQ_OFFSET, self._seq)

    def close(self) -> None:
        if self._addr:
            # Mark the stream stale immediately so the camera shows "waiting" without delay.
            HEADER.pack_into(self._view, 0, MAGIC, VERSION, 0, 0, self._seq, 0, self._index, 0)
            self._view.release()
            self._pixels = None
            self._buf = None
            _kernel32().UnmapViewOfFile(ctypes.c_void_p(self._addr))
            _kernel32().CloseHandle(self._handle)
            self._addr = 0


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileMappingW.restype = ctypes.c_void_p
    k.CreateFileMappingW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_uint32, ctypes.c_wchar_p]
    k.MapViewOfFile.restype = ctypes.c_void_p
    k.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
    k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.LocalFree.argtypes = [ctypes.c_void_p]
    return k


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("nLength", ctypes.c_uint32), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", ctypes.c_int)]


def _create_mapping() -> tuple[int, int]:
    k = _kernel32()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    sd = ctypes.c_void_p()
    sa = None
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    if convert(_SDDL, 1, ctypes.byref(sd), None):
        sa = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), sd, 0)
    try:
        invalid_handle = ctypes.c_void_p(-1)
        handle = k.CreateFileMappingW(invalid_handle, ctypes.byref(sa) if sa else None, 0x04,  # PAGE_READWRITE
                                      0, MAP_SIZE, MAPPING_NAME)
        if not handle:
            raise VirtualCameraError(f"CreateFileMapping failed ({ctypes.get_last_error()})")
        addr = k.MapViewOfFile(handle, 0x0002, 0, 0, MAP_SIZE)  # FILE_MAP_WRITE
        if not addr:
            k.CloseHandle(handle)
            raise VirtualCameraError(f"MapViewOfFile failed ({ctypes.get_last_error()})")
        return handle, addr
    finally:
        if sd:
            k.LocalFree(sd)
