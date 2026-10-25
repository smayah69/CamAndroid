import hashlib
import io
import json

import pytest

from camandroid_desktop import adb, updates


def test_versions_compare_numerically():
    assert updates.parse_version("v0.3.41") == (0, 3, 41)
    assert updates.parse_version("0.3.45-dev") == (0, 3, 45)
    assert updates.is_newer("v0.3.41", "0.3.40")
    assert updates.is_newer("v0.10.1", "0.9.99")
    assert not updates.is_newer("v0.3.40", "0.3.40")
    assert not updates.is_newer("v0.3.39", "0.3.40")


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def release_json(installer: bytes) -> bytes:
    digest = "sha256:" + hashlib.sha256(installer).hexdigest()
    return json.dumps({
        "tag_name": "v9.9.9",
        "html_url": "https://github.com/smayah69/CamAndroid/releases/tag/v9.9.9",
        "body": "notes",
        "assets": [
            {"name": "CamAndroid-9.9.9.apk", "browser_download_url": "https://github.com/x/a.apk", "size": 1},
            {"name": "CamAndroid-Setup-9.9.9.exe", "size": len(installer), "digest": digest,
             "browser_download_url": "https://github.com/smayah69/CamAndroid/releases/download/v9.9.9/s.exe"},
        ],
    }).encode()


def test_fetch_and_download_verifies_checksum(monkeypatch, tmp_path):
    payload = b"MZ" + b"\0" * 1000
    responses = {updates.LATEST_API: release_json(payload)}

    def fake_urlopen(req, timeout=None):
        return FakeResponse(responses.get(req.full_url, payload))

    monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)
    release = updates.fetch_latest()
    assert release.version == "9.9.9" and updates.is_newer(release.version)
    assert release.installer.name == "CamAndroid-Setup-9.9.9.exe"
    seen = []
    path = updates.download(release.installer, lambda done, total: seen.append((done, total)), tmp_path)
    assert path.read_bytes() == payload
    assert seen[-1] == (len(payload), len(payload))

    release.installer.sha256 = "00" * 32
    with pytest.raises(updates.UpdateError, match="SHA-256"):
        updates.download(release.installer, dest_dir=tmp_path)
    assert not (tmp_path / "CamAndroid-Setup-9.9.9.exe").exists()


def test_download_only_from_github(tmp_path):
    with pytest.raises(updates.UpdateError):
        updates.download(updates.Asset("x.exe", "https://evil.example/x.exe", 1, None), dest_dir=tmp_path)


def test_adb_device_list_parsing(monkeypatch):
    out = ("List of devices attached\n"
           "R58M123ABC             device usb:1-1 product:beyond1 model:SM_G973F device:beyond1 transport_id:3\n"
           "0A1B2C3D               unauthorized usb:1-2 transport_id:4\n"
           "192.168.1.20:41235     device product:panther model:Pixel_7 device:panther transport_id:5\n\n")
    monkeypatch.setattr(adb, "_run", lambda args: out)
    phones = adb.list_phones()
    assert [(p.model, p.state, p.ready, p.wireless) for p in phones] == [
        ("SM G973F", "device", True, False),
        ("0A1B2C3D", "unauthorized", False, False),
        ("Pixel 7", "device", True, True),
    ]
