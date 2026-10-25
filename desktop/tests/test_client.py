import threading

import pytest

from camandroid_desktop import protocol as p
from camandroid_desktop.client import (
    FingerprintMismatchError,
    NotPairedError,
    PhoneConnection,
    RemoteError,
    StreamSettings,
)
from camandroid_desktop.session import SessionCallbacks, StreamSession, Target
from camandroid_desktop.store import DeviceStore, PairedDevice
from camandroid_desktop.video import OutputOptions

from .mock_phone import MockPhone


@pytest.fixture
def phone():
    ph = MockPhone()
    yield ph
    ph.close()


@pytest.fixture
def store(tmp_path):
    return DeviceStore(tmp_path / "devices.json")


def test_pair_then_authenticate(phone, store):
    code = phone.new_code()
    with PhoneConnection("127.0.0.1", phone.port) as conn:
        device = conn.pair(store, p.format_code(code).lower())
    assert device.fingerprint == phone.fingerprint
    assert device.name == "Mock Phone"

    reloaded = DeviceStore(store.path)
    assert reloaded.get(phone.fingerprint).token == device.token
    with PhoneConnection("127.0.0.1", phone.port) as conn:
        assert conn.authenticate(reloaded).name == "Mock Phone"
        assert conn.cameras == ["back", "front"]


def test_store_file_is_private(store, tmp_path):
    import os
    import sys

    store.add(PairedDevice("f" * 64, "x", "0" * 64))
    if sys.platform != "win32":
        assert os.stat(store.path).st_mode & 0o077 == 0


def test_wrong_code(phone, store):
    phone.new_code()
    with PhoneConnection("127.0.0.1", phone.port) as conn, pytest.raises(RemoteError) as err:
        conn.pair(store, "ZZZZ-ZZZZ-ZZZZ")
    assert err.value.code == "bad_code"
    assert not store.devices


def test_invalid_code_format_is_rejected_locally(phone, store):
    with PhoneConnection("127.0.0.1", phone.port) as conn, pytest.raises(Exception, match="12 characters"):
        conn.pair(store, "1234")


def test_unpaired_phone(phone, store):
    with PhoneConnection("127.0.0.1", phone.port) as conn, pytest.raises(NotPairedError):
        conn.authenticate(store)


def test_fingerprint_mismatch_is_detected(phone, store):
    impostor_record = PairedDevice("00" * 32, "My Phone", "11" * 32, "127.0.0.1", phone.port)
    store.add(impostor_record)
    with PhoneConnection("127.0.0.1", phone.port) as conn, pytest.raises(FingerprintMismatchError):
        conn.authenticate(store, impostor_record)


def test_stream_session_delivers_frames(store):
    phone = MockPhone(rotation=90)
    try:
        with PhoneConnection("127.0.0.1", phone.port) as conn:
            phone.new_code()
            device = conn.pair(store, phone.code)
        got = []
        done = threading.Event()

        def on_preview(img):
            got.append(img)
            if len(got) >= 3:
                done.set()

        session = StreamSession(
            store, Target(device=device), StreamSettings(width=320, height=240, camera="front"),
            OutputOptions(width=320, height=240), SessionCallbacks(on_preview=on_preview),
            virtual_camera=False, preview_interval=0,
        )
        session.start()
        assert done.wait(10), "no frames received"
        session.update(torch=True)
        for _ in range(50):
            if phone.updates:
                break
            done.clear()
            done.wait(0.1)
        session.stop()
        session.join(5)
        assert not session.is_alive()
        assert phone.started_with["camera"] == "front"
        assert phone.updates and phone.updates[0]["torch"] is True
        assert got[0].size == (320, 240)
    finally:
        phone.close()
