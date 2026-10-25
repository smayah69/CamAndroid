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
    if a.root.winfo_exists():
        a.on_close()


def pump(app, rounds=20):
    for _ in range(rounds):
        app.root.update()
        app._pump()


def test_tabs_and_defaults(app):
    assert [app.tabs.tab(t, "text") for t in app.tabs.tabs()] == ["Webcam", "Logs", "About"]
    assert app.mirror.get() is True
    assert app.fps.get() == "60"


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


def test_about_tab_offers_update(app):
    release = updates.Release("99.0.0", updates.RELEASES_URL, "", [])
    app._show_release(release, quiet=True)
    assert "99.0.0" in app.update_status.get()
    assert app.install_btn.winfo_manager() == "grid"
    app._show_release(updates.Release("0.0.1", updates.RELEASES_URL, "", []), quiet=True)
    assert "latest" in app.update_status.get()
