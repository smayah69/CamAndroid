import mmap
import os
import struct
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from camandroid_desktop import camera_install, vcam

windows = pytest.mark.skipif(sys.platform != "win32", reason="virtual camera is Windows-only")
VCAMTEST = os.environ.get("CAMANDROID_VCAMTEST")


def test_header_layout_matches_c_struct():
    assert vcam.HEADER.size == vcam.HEADER_SIZE == 64
    assert vcam.MAP_SIZE == 64 + 3840 * 2160 * 3


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_refuses_on_other_platforms():
    with pytest.raises(vcam.VirtualCameraError):
        vcam.VirtualCamera(640, 360, 30)


@pytest.fixture
def camera(monkeypatch):
    monkeypatch.setattr(camera_install, "status", lambda: camera_install.CameraStatus("x", None, "user"))
    cam = vcam.VirtualCamera(320, 240, 30)
    yield cam
    cam.close()


@windows
def test_writer_publishes_frames(camera):
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[:, :, 0] = 200
    camera.send(frame)
    reader = mmap.mmap(-1, vcam.MAP_SIZE, tagname=vcam.MAPPING_NAME, access=mmap.ACCESS_READ)
    magic, version, w, h, seq, fps, index, ts = vcam.HEADER.unpack_from(reader, 0)
    assert (magic, version, w, h, fps, index) == (vcam.MAGIC, 1, 320, 240, 30, 1)
    assert seq % 2 == 0
    assert abs(ts - time.time() * 1000) < 5000
    assert reader[64:67] == bytes([200, 0, 0])
    reader.close()


def _feed(cam: vcam.VirtualCamera, rgb, stop: threading.Event, size=(640, 360)) -> None:
    frame = np.empty((size[1], size[0], 3), dtype=np.uint8)
    frame[:] = rgb
    while not stop.is_set():
        cam.send(frame)
        time.sleep(1 / 30)


@windows
@pytest.mark.skipif(not VCAMTEST, reason="set CAMANDROID_VCAMTEST to the built vcamtest.exe")
def test_dll_shows_python_frames(camera):
    """End to end: Python writer -> shared memory -> camera DLL -> DirectShow graph."""
    stop = threading.Event()
    t = threading.Thread(target=_feed, args=(camera, (30, 200, 90), stop), daemon=True)
    t.start()
    try:
        result = subprocess.run([VCAMTEST, "--size", "1280x720", "--seconds", "2", "--expect", "30,200,90"],
                                capture_output=True, text=True, timeout=60)
    finally:
        stop.set()
        t.join()
    print(result.stdout, result.stderr)
    assert result.returncode == 0, result.stdout


@windows
@pytest.mark.skipif(not VCAMTEST, reason="set CAMANDROID_VCAMTEST to the built vcamtest.exe")
def test_opencv_sees_camera(camera):
    """A third-party DirectShow client (OpenCV) opens the camera and gets our frames."""
    cv2 = pytest.importorskip("cv2")
    stop = threading.Event()
    t = threading.Thread(target=_feed, args=(camera, (220, 30, 40), stop), daemon=True)
    t.start()
    try:
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        assert cap.isOpened(), "OpenCV could not open the CamAndroid camera"
        frame = None
        for _ in range(60):
            ok, frame = cap.read()
            if ok and frame is not None and frame.mean() > 10:
                break
            time.sleep(0.05)
        cap.release()
    finally:
        stop.set()
        t.join()
    assert frame is not None
    b, g, r = (int(v) for v in frame[frame.shape[0] // 2, frame.shape[1] // 2])
    assert abs(r - 220) < 15 and abs(g - 30) < 15 and abs(b - 40) < 15, (r, g, b)


@windows
def test_status_reports_cleanly():
    st = camera_install.status()
    assert st.scope in {"none", "user", "machine", "mixed"}
    assert "CamAndroid camera" in st.describe()


def test_struct_seq_offset():
    buf = bytearray(64)
    vcam.HEADER.pack_into(buf, 0, 1, 2, 3, 4, 5, 6, 7, 8)
    assert struct.unpack_from("<I", buf, vcam.SEQ_OFFSET)[0] == 5
