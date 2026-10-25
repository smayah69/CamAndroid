"""Desktop client against the real Kotlin server (android/core, `./gradlew :core:runFakePhone`).

Skipped unless CAMANDROID_INTEROP_PORT and CAMANDROID_INTEROP_CODE_FILE are set (CI sets them).
"""

import os
import time
from pathlib import Path

import pytest

from camandroid_desktop import protocol as p
from camandroid_desktop.client import PhoneConnection, RemoteError, StreamSettings
from camandroid_desktop.store import DeviceStore
from camandroid_desktop.video import OutputOptions, render

PORT = os.environ.get("CAMANDROID_INTEROP_PORT")
CODE_FILE = os.environ.get("CAMANDROID_INTEROP_CODE_FILE")
pytestmark = pytest.mark.skipif(not (PORT and CODE_FILE), reason="Kotlin fake phone not running")


def read_code() -> str:
    path = Path(CODE_FILE)
    for _ in range(100):
        if path.exists() and path.read_text().strip():
            return path.read_text().strip()
        time.sleep(0.1)
    raise AssertionError("pairing code file not written")


def test_against_kotlin_server(tmp_path):
    store = DeviceStore(tmp_path / "devices.json")
    port = int(PORT)

    with PhoneConnection("127.0.0.1", port) as conn, pytest.raises(RemoteError) as err:
        conn.pair(store, "ZZZZ-ZZZZ-ZZZZ")
    assert err.value.code in ("bad_code", "no_pairing")

    # A wrong guess burns the code; the fake phone then writes a fresh one to the code file.
    code = read_code()
    with PhoneConnection("127.0.0.1", port) as conn:
        try:
            device = conn.pair(store, code)
        except RemoteError as e:
            pytest.fail(f"pairing failed: {e.code}")
    assert device.name == "Fake Phone"

    time.sleep(0.3)
    with PhoneConnection("127.0.0.1", port) as conn:
        conn.authenticate(store)
        assert set(conn.cameras) == {"back", "front"}
        conn.settimeout(10)
        conn.start(StreamSettings(width=320, height=240, fps=15))
        frames = 0
        while frames < 3:
            msg = conn.read()
            if isinstance(msg, p.Frame):
                img = render(msg, OutputOptions(width=640, height=360))
                assert img.size == (640, 360)
                frames += 1
        print(f"TLS {conn.sock.version()} {conn.sock.cipher()[0]}")
