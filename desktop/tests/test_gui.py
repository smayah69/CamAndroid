import logging

import pytest

tk = pytest.importorskip("tkinter")

from camandroid_desktop import adb, discovery, gui, updates  # noqa: E402


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMANDROID_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(gui.App, "_offer_camera_install", lambda self: None)
    monkeypatch.setattr(gui.App, "check_updates", lambda self, quiet=False: None)
    monkeypatch.setattr(discovery, "PhoneBrowser", lambda on_change: type("B", (), {"close": lambda s: None})())
    monkeypatch.setattr(adb, "list_phones", lambda: [adb.UsbPhone("R58M123", "device", "Galaxy S10")])
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")
    a = gui.App(root)
    yield a
    try:
        a.on_close()
    except tk.TclError:
        pass  # the test already closed the window


def pump(app, rounds=20):
    for _ in range(rounds):
        app.root.update()
        app._pump()


def test_tabs_and_defaults(app):
    assert [app.tabs.tab(t, "text") for t in app.tabs.tabs()] == ["Webcam", "Logs", "About"]
    assert app.mirror.get() is False
    assert app.fps.get() == "60"


def _buttons(widget):
    for child in widget.winfo_children():
        if child.winfo_class() == "TButton":
            yield child.cget("text")
        yield from _buttons(child)


def test_single_pair_button(app):
    labels = list(_buttons(app.root))
    assert labels.count("Pair…") == 1
    assert not any("address" in label.lower() for label in labels)


def test_usb_and_wifi_phones_show_connection(app):
    phone = discovery.DiscoveredPhone("CamAndroid Pixel", "192.168.1.20", 4747, "abcd")
    app.events.put(("discovered", [phone]))
    import time

    for _ in range(50):  # the USB poller runs in the background
        pump(app, 1)
        if any(app.tree.set(i, "connection") == "USB" for i in app.tree.get_children()):
            break
        time.sleep(0.05)
    rows = {app.tree.item(i, "text"): app.tree.set(i, "connection") for i in app.tree.get_children()}
    assert rows == {"CamAndroid Pixel": "Wi-Fi", "Galaxy S10": "USB"}
    app.tree.selection_set("usb:R58M123")
    assert app._selected() == ("usb", "R58M123")
    assert app._usb_target("R58M123").serial == "R58M123"


def test_logs_tab_shows_records(app):
    logging.getLogger("camandroid_desktop.test").warning("hello from the test")
    pump(app)
    assert "hello from the test" in app.log_text.get("1.0", "end")


def test_about_tab_offers_update(app, monkeypatch):
    asked, installs = [], []
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: asked.append(a) or True)
    monkeypatch.setattr(app, "install_update", lambda: installs.append(1))
    release = updates.Release("99.0.0", updates.RELEASES_URL, "", [])
    app._show_release(release, quiet=True)  # the check made at every start
    assert len(asked) == 1 and "99.0.0" in asked[0][1], "startup check should ask to update"
    assert installs == [1]
    assert "99.0.0" in app.update_status.get()
    assert app.install_btn.winfo_manager() == "grid"
    app._show_release(updates.Release("0.0.1", updates.RELEASES_URL, "", []), quiet=True)
    assert "latest" in app.update_status.get()


def test_installer_starts_with_a_clean_environment(app, monkeypatch):
    """Otherwise the new version, started by the installer, fails with "Failed to load Python DLL"."""
    import os

    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI12345")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", r"C:\Program Files\CamAndroid\camandroid.exe")
    seen = {}

    def fake_startfile(path):
        seen.update(path=path, env=dict(os.environ))

    monkeypatch.setattr(os, "startfile", fake_startfile, raising=False)
    app._run_installer("CamAndroid-Setup-9.9.9.exe")
    assert seen["path"] == "CamAndroid-Setup-9.9.9.exe"
    assert not [k for k in seen["env"] if k.startswith("_PYI_")]
    assert seen["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
