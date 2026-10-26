"""Tkinter GUI: discover and pair phones, then stream one to the virtual webcam."""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from . import __version__, adb, icon, logs, protocol, updates
from .client import StreamSettings
from .session import SessionCallbacks, StreamSession, Target, connect
from .store import DeviceStore
from .video import OutputOptions

log = logging.getLogger(__name__)

RESOLUTIONS = ["640x480", "1280x720", "1920x1080"]
FPS_CHOICES = ["15", "24", "30", "60"]
ROTATIONS = ["0", "90", "180", "270"]
PREVIEW_SIZE = (384, 216)
USB_POLL_S = 2.0
MAX_LOG_LINES = 5000
AUTHOR = "Omar Kharrat"
AUTHOR_HANDLE = "@smayah69"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.store = DeviceStore()
        self.events: queue.Queue = queue.Queue()
        self.session: StreamSession | None = None
        self.discovered: dict[str, object] = {}  # device_id -> DiscoveredPhone
        self.usb_phones: list[adb.UsbPhone] = []
        self.browser = None
        self.release: updates.Release | None = None
        self._closing = threading.Event()
        self._preview_photo = None
        self._log_handler = logs.CallbackHandler(lambda level, line: self.events.put(("log", (level, line))))
        logging.getLogger("camandroid_desktop").addHandler(self._log_handler)

        root.title(f"CamAndroid {__version__}")
        root.minsize(860, 560)
        root.geometry("940x600")
        self._icons = [ImageTk.PhotoImage(icon.render(s)) for s in (64, 32, 16)]
        root.iconphoto(True, *self._icons)
        self._build()
        log.info("CamAndroid %s started (log file: %s)", __version__, logs.setup())
        self._start_discovery()
        threading.Thread(target=self._poll_usb, name="usb-poll", daemon=True).start()
        self.refresh_devices()
        self.refresh_camera_status()
        root.after(50, self._pump)
        root.after(300, self._offer_camera_install)
        root.after(3000, lambda: self.check_updates(quiet=True))
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # -- layout ------------------------------------------------------------------------------

    def _build(self) -> None:
        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w", padding=4).pack(
            fill="x", side="bottom")
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=6, pady=6)
        webcam, logs_tab, about = (ttk.Frame(self.tabs, padding=8) for _ in range(3))
        self.tabs.add(webcam, text="Webcam")
        self.tabs.add(logs_tab, text="Logs")
        self.tabs.add(about, text="About")
        self._build_webcam(webcam)
        self._build_logs(logs_tab)
        self._build_about(about)

    def _build_webcam(self, main: ttk.Frame) -> None:
        pad = {"padx": 8, "pady": 4}
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(main, text="Phones", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        left.columnconfigure(1, weight=1)
        left.columnconfigure(2, weight=1)
        self.tree = ttk.Treeview(left, columns=("connection", "address", "status"), show="tree headings", height=8)
        self.tree.heading("#0", text="Name")
        self.tree.heading("connection", text="Connection")
        self.tree.heading("address", text="Address")
        self.tree.heading("status", text="Status")
        self.tree.column("#0", width=150)
        self.tree.column("connection", width=80, stretch=False)
        self.tree.column("address", width=130)
        self.tree.column("status", width=130)
        self.tree.grid(row=0, column=0, columnspan=3, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _e: self.toggle_stream() if not self.session else None)

        # One button: pairs the selected phone, or asks for its address when none is selected
        # (or discovery can't see it, e.g. on guest Wi-Fi).
        ttk.Button(left, text="Pair…", command=self.pair_selected).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0), padx=(0, 4))
        ttk.Button(left, text="Forget", command=self.forget_selected).grid(row=1, column=2, sticky="ew", pady=(8, 0))
        ttk.Label(left, text="Phones on your Wi-Fi show up automatically, and so do phones plugged in by USB "
                             "with USB debugging on. On the phone: open CamAndroid, tap Start, then "
                             "'Pair new computer'.",
                  foreground="gray", wraplength=380).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        right = ttk.LabelFrame(main, text="Webcam", padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)

        self.resolution = tk.StringVar(value="1280x720")
        self.fps = tk.StringVar(value="60")
        self.quality = tk.IntVar(value=80)
        self.camera = tk.StringVar(value="back")
        self.rotate = tk.StringVar(value="0")
        self.mirror = tk.BooleanVar(value=True)
        self.fill = tk.BooleanVar(value=False)
        self.torch = tk.BooleanVar(value=False)

        row = 0
        self.camera_status = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.camera_status).grid(row=row, column=0, sticky="w", **pad)
        self.camera_btn = ttk.Button(right, text="Install camera", command=self.install_camera)
        self.camera_btn.grid(row=row, column=1, sticky="e", **pad)
        row += 1
        ttk.Label(right, text="Resolution").grid(row=row, column=0, sticky="w", **pad)
        self.res_box = ttk.Combobox(right, textvariable=self.resolution, values=RESOLUTIONS, state="readonly", width=12)
        self.res_box.grid(row=row, column=1, sticky="w", **pad)
        row += 1
        ttk.Label(right, text="Frame rate").grid(row=row, column=0, sticky="w", **pad)
        self.fps_box = ttk.Combobox(right, textvariable=self.fps, values=FPS_CHOICES, state="readonly", width=12)
        self.fps_box.grid(row=row, column=1, sticky="w", **pad)
        row += 1
        ttk.Label(right, text="Quality").grid(row=row, column=0, sticky="w", **pad)
        ttk.Scale(right, from_=40, to=95, variable=self.quality, orient="horizontal",
                  command=lambda _v: None).grid(row=row, column=1, sticky="ew", **pad)
        right.bind_all("<ButtonRelease-1>", self._on_release, add="+")
        row += 1
        ttk.Label(right, text="Camera").grid(row=row, column=0, sticky="w", **pad)
        cams = ttk.Frame(right)
        cams.grid(row=row, column=1, sticky="w", **pad)
        for value, label in (("back", "Back"), ("front", "Front")):
            ttk.Radiobutton(cams, text=label, value=value, variable=self.camera,
                            command=lambda: self._live_update(camera=self.camera.get())).pack(side="left")
        row += 1
        ttk.Label(right, text="Rotate").grid(row=row, column=0, sticky="w", **pad)
        rot = ttk.Combobox(right, textvariable=self.rotate, values=ROTATIONS, state="readonly", width=12)
        rot.grid(row=row, column=1, sticky="w", **pad)
        rot.bind("<<ComboboxSelected>>", lambda _e: self._output_update())
        row += 1
        opts = ttk.Frame(right)
        opts.grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(opts, text="Mirror", variable=self.mirror, command=self._output_update).pack(side="left")
        ttk.Checkbutton(opts, text="Crop to fill", variable=self.fill, command=self._output_update).pack(
            side="left", padx=8)
        ttk.Checkbutton(opts, text="Flashlight", variable=self.torch,
                        command=lambda: self._live_update(torch=self.torch.get())).pack(side="left")
        row += 1
        self.start_btn = ttk.Button(right, text="Start webcam", command=self.toggle_stream)
        self.start_btn.grid(row=row, column=0, columnspan=2, sticky="ew", **pad)
        row += 1
        self.preview = ttk.Label(right, anchor="center")
        self.preview.grid(row=row, column=0, columnspan=2, **pad)
        self._set_preview(Image.new("RGB", PREVIEW_SIZE, (24, 24, 24)))

    def _build_logs(self, tab: ttk.Frame) -> None:
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        self.log_text = tk.Text(tab, wrap="none", height=20, state="disabled", font=("Consolas", 9))
        self.log_text.tag_configure("warning", foreground="#b36b00")
        self.log_text.tag_configure("error", foreground="#c62828")
        yscroll = ttk.Scrollbar(tab, orient="vertical", command=self.log_text.yview)
        xscroll = ttk.Scrollbar(tab, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        buttons = ttk.Frame(tab)
        buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Copy all", command=self._copy_logs).pack(side="left")
        ttk.Button(buttons, text="Clear", command=self._clear_logs).pack(side="left", padx=8)
        ttk.Button(buttons, text="Open log folder", command=self._open_log_folder).pack(side="left")

    def _build_about(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(1, weight=1)
        self._about_icon = ImageTk.PhotoImage(icon.render(96))
        ttk.Label(tab, image=self._about_icon).grid(row=0, column=0, rowspan=4, sticky="nw", padx=(4, 16))
        ttk.Label(tab, text="CamAndroid", font=("Segoe UI", 18, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Label(tab, text=f"Version {__version__}").grid(row=1, column=1, sticky="w")
        ttk.Label(tab, text="Use your Android phone as a webcam, over Wi-Fi or USB.").grid(
            row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Label(tab, text=f"Made by {AUTHOR} ({AUTHOR_HANDLE} on GitHub)").grid(row=3, column=1, sticky="w")

        links = ttk.Frame(tab)
        links.grid(row=4, column=1, sticky="w", pady=(12, 0))
        for text, url in (("GitHub", updates.REPO_URL), ("Releases", updates.RELEASES_URL),
                          ("Report a problem", updates.ISSUES_URL)):
            link = ttk.Label(links, text=text, foreground="#1565c0", cursor="hand2", font=("Segoe UI", 10, "underline"))
            link.pack(side="left", padx=(0, 16))
            link.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u))
        ttk.Label(tab, text=updates.REPO_URL, foreground="gray").grid(row=5, column=1, sticky="w")

        box = ttk.LabelFrame(tab, text="Updates", padding=8)
        box.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        box.columnconfigure(0, weight=1)
        self.update_status = tk.StringVar(value="Not checked yet.")
        ttk.Label(box, textvariable=self.update_status, wraplength=600).grid(row=0, column=0, sticky="w")
        self.check_btn = ttk.Button(box, text="Check for updates", command=self.check_updates)
        self.check_btn.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.install_btn = ttk.Button(box, text="Download and install", command=self.install_update)
        self.update_progress = ttk.Progressbar(box, mode="determinate", maximum=100)

    # -- devices -----------------------------------------------------------------------------

    def _start_discovery(self) -> None:
        try:
            from .discovery import PhoneBrowser

            self.browser = PhoneBrowser(on_change=lambda phones: self.events.put(("discovered", phones)))
        except Exception as e:  # noqa: BLE001 - discovery is optional (e.g. firewall, no zeroconf)
            log.warning("Network discovery unavailable: %s", e)
            self.status.set(f"Network discovery unavailable: {e}. Click Pair… and type the phone's address.")

    def _poll_usb(self) -> None:
        last: list[adb.UsbPhone] | None = None
        reported = False
        while not self._closing.is_set():
            try:
                phones = adb.list_phones()
                reported = False
            except adb.AdbError as e:
                phones = []
                if not reported:
                    log.warning("USB phones unavailable: %s", e)
                    reported = True
            if phones != last:
                for p in phones:
                    log.info("USB: %s (%s) %s", p.model, p.serial, "ready" if p.ready else p.state)
                last = phones
                self.events.put(("usb", phones))
            self._closing.wait(USB_POLL_S if not reported else 10 * USB_POLL_S)

    def refresh_devices(self) -> None:
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        seen = set()
        for dev in self.store.devices.values():
            found = self.discovered.get(dev.device_id)
            address = f"{found.host}:{found.port}" if found else (f"{dev.host}:{dev.port}" if dev.host else "")
            status = "Online" if found else "Offline"
            self.tree.insert("", "end", iid=dev.device_id, text=dev.name, values=("Wi-Fi", address, status))
            seen.add(dev.device_id)
        for device_id, found in self.discovered.items():
            if device_id not in seen:
                self.tree.insert("", "end", iid=f"new:{device_id}", text=found.name,
                                 values=("Wi-Fi", f"{found.host}:{found.port}", "Not paired"))
        for phone in self.usb_phones:
            status = {"device": "Ready", "unauthorized": "Allow USB debugging",
                      "offline": "Offline"}.get(phone.state, phone.state)
            self.tree.insert("", "end", iid=f"usb:{phone.serial}", text=phone.model,
                             values=("Wi-Fi (adb)" if phone.wireless else "USB", phone.serial, status))
        for iid in selected:
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
        children = self.tree.get_children()
        if not self.tree.selection() and children:
            self.tree.selection_set(children[0])

    def _selected(self) -> tuple[str, str] | tuple[None, None]:
        """(kind, key): ("paired", device_id), ("new", device_id) or ("usb", serial)."""
        sel = self.tree.selection()
        if not sel:
            return None, None
        iid = sel[0]
        for prefix, kind in (("new:", "new"), ("usb:", "usb")):
            if iid.startswith(prefix):
                return kind, iid[len(prefix):]
        return "paired", iid

    def _usb_target(self, serial: str) -> Target | None:
        phone = next((p for p in self.usb_phones if p.serial == serial), None)
        if phone is None:
            return None
        if phone.state == "unauthorized":
            messagebox.showinfo("USB", "Unlock the phone and accept the 'Allow USB debugging?' prompt "
                                       "(tick 'Always allow from this computer').")
            return None
        if not phone.ready:
            messagebox.showinfo("USB", f"{phone.model} is {phone.state}. Reconnect the cable and try again.")
            return None
        return Target(usb=True, serial=phone.serial, label=phone.model)

    def pair_selected(self) -> None:
        """Pairs the selected phone if it isn't paired yet; otherwise asks for an address."""
        kind, key = self._selected()
        if kind == "usb":
            target = self._usb_target(key)
            if target:
                self._pair(target)
            return
        found = self.discovered.get(key) if key else None
        if kind == "new" and found:
            self._pair(Target(host=found.host, port=found.port))
            return
        # A phone that is already paired (re-pair it, e.g. after reinstalling the app) or one the
        # network discovery can't see: ask for the address, filled in when we know it.
        dev = self.store.find(key) if kind == "paired" else None
        known = f"{found.host}" if found else (dev.host if dev and dev.host else "")
        text = simpledialog.askstring(
            "Pair a phone",
            "Address shown in the CamAndroid app on the phone (e.g. 192.168.1.20).\n"
            "Phones on your Wi-Fi or plugged in by USB also appear in the list: select one and click Pair.",
            initialvalue=known, parent=self.root)
        if not text:
            return
        host, _, port = text.strip().partition(":")
        self._pair(Target(host=host, port=int(port) if port.isdigit() else protocol.DEFAULT_PORT))

    def _pair(self, target: Target) -> None:
        code = simpledialog.askstring(
            "Pairing code",
            "On the phone tap 'Pair new computer' and type the 12-character code:",
            parent=self.root,
        )
        if not code:
            return
        self.status.set(f"Pairing with {target.describe()}…")
        log.info("Pairing with %s", target.describe())

        def work():
            try:
                with connect(self.store, target) as conn:
                    device = conn.pair(self.store, code)
                if target.usb:
                    device.host, device.port = "", 0
                    self.store.save()
                log.info("Paired with %s", device.name)
                self.events.put(("paired", device.name))
            except Exception as e:  # noqa: BLE001
                log.error("Pairing with %s failed: %s", target.describe(), e)
                self.events.put(("pair_failed", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def forget_selected(self) -> None:
        kind, key = self._selected()
        dev = self.store.find(key) if kind == "paired" else None
        if not dev:
            return
        if messagebox.askyesno("Forget phone", f"Forget {dev.name}? You will need to pair again."):
            self.store.remove(dev.fingerprint)
            log.info("Forgot %s", dev.name)
            self.refresh_devices()

    # -- virtual camera ----------------------------------------------------------------------

    def refresh_camera_status(self) -> None:
        from . import camera_install

        st = camera_install.status()
        self.camera_status.set("Camera 'CamAndroid': installed" if st.installed else "Camera: not installed")
        self.camera_btn.config(text="Reinstall camera" if st.installed else "Install camera")

    def _offer_camera_install(self) -> None:
        from . import camera_install

        if sys.platform == "win32" and not camera_install.status().installed:
            if messagebox.askyesno(
                "CamAndroid",
                "Install the 'CamAndroid' webcam so Zoom, Teams, Meet, Discord and other apps can use "
                "your phone?\n\nNo administrator rights or extra software are needed.",
            ):
                self.install_camera()

    def install_camera(self) -> None:
        from . import camera_install

        try:
            st = camera_install.install_user()
            log.info(st.describe())
            self.status.set(st.describe())
            messagebox.showinfo("CamAndroid", "Camera installed. Restart your video app if it is already open, "
                                              "then choose 'CamAndroid' as the camera.")
        except camera_install.CameraInstallError as e:
            log.error("Could not install the camera: %s", e)
            messagebox.showerror("CamAndroid", f"Could not install the camera: {e}")
        self.refresh_camera_status()

    # -- streaming ---------------------------------------------------------------------------

    def toggle_stream(self) -> None:
        if self.session:
            self.session.stop()
            self.start_btn.config(state="disabled")
            return
        from . import camera_install

        if sys.platform == "win32" and not camera_install.status().installed:
            self.install_camera()
            if not camera_install.status().installed:
                return
        kind, key = self._selected()
        if kind == "usb":
            # Over USB the phone is identified by its certificate after connecting.
            target = self._usb_target(key)
            if not target:
                return
        else:
            dev = self.store.find(key) if kind == "paired" else None
            if not dev:
                messagebox.showinfo("Pair first", "Select a paired phone, or pair a new one first.")
                return
            found = self.discovered.get(dev.device_id)
            if found:
                self.store.update_address(dev.fingerprint, found.host, found.port)
            target = Target(device=dev)
        width, height = (int(x) for x in self.resolution.get().split("x"))
        settings = StreamSettings(width=width, height=height, fps=int(self.fps.get()), quality=self.quality.get(),
                                  camera=self.camera.get(), torch=self.torch.get())
        output = OutputOptions(width=width, height=height, rotate=int(self.rotate.get()),
                               mirror=self.mirror.get(), fill=self.fill.get())
        callbacks = SessionCallbacks(
            on_status=lambda s: self.events.put(("status", s)),
            on_error=lambda s: self.events.put(("error", s)),
            on_preview=lambda img: self.events.put(("preview", img)),
            on_finished=lambda: self.events.put(("finished", None)),
        )
        log.info("Starting webcam from %s: %dx%d at %s fps", target.describe(), width, height, settings.fps)
        self.session = StreamSession(self.store, target, settings, output, callbacks)
        self.session.start()
        self.start_btn.config(text="Stop webcam")
        self.res_box.config(state="disabled")
        self.fps_box.config(state="disabled")
        self.status.set(f"Connecting to {target.describe()}…")

    def _live_update(self, **changes) -> None:
        if self.session:
            self.session.update(**changes)

    def _on_release(self, _event) -> None:
        if self.session and self.session.settings.quality != self.quality.get():
            self.session.update(quality=self.quality.get())

    def _output_update(self) -> None:
        if self.session:
            self.session.output.rotate = int(self.rotate.get())
            self.session.output.mirror = self.mirror.get()
            self.session.output.fill = self.fill.get()

    def _set_preview(self, img: Image.Image) -> None:
        thumb = img.copy()
        thumb.thumbnail(PREVIEW_SIZE)
        self._preview_photo = ImageTk.PhotoImage(thumb)
        self.preview.config(image=self._preview_photo)

    # -- logs --------------------------------------------------------------------------------

    def _append_log(self, lines: list[tuple[int, str]]) -> None:
        at_end = self.log_text.yview()[1] >= 0.999
        self.log_text.config(state="normal")
        for level, line in lines:
            tag = "error" if level >= logging.ERROR else "warning" if level >= logging.WARNING else ""
            self.log_text.insert("end", line + "\n", tag)
        excess = int(self.log_text.index("end-1c").split(".")[0]) - MAX_LOG_LINES
        if excess > 0:
            self.log_text.delete("1.0", f"{excess + 1}.0")
        self.log_text.config(state="disabled")
        if at_end:
            self.log_text.see("end")

    def _copy_logs(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log_text.get("1.0", "end-1c"))
        self.status.set("Logs copied to the clipboard")

    def _clear_logs(self) -> None:
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _open_log_folder(self) -> None:
        folder = logs.log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - our own folder
        else:
            webbrowser.open(folder.as_uri())

    # -- updates -----------------------------------------------------------------------------

    def check_updates(self, quiet: bool = False) -> None:
        self.check_btn.config(state="disabled")
        self.update_status.set("Checking GitHub for updates…")

        def work():
            try:
                self.events.put(("release", (updates.fetch_latest(), quiet)))
            except updates.UpdateError as e:
                log.warning("Update check failed: %s", e)
                self.events.put(("release_failed", (str(e), quiet)))

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _show_release(self, release: updates.Release, quiet: bool) -> None:
        self.check_btn.config(state="normal")
        self.release = release
        if not updates.is_newer(release.version):
            self.update_status.set(f"You have the latest version ({__version__}).")
            self.install_btn.grid_remove()
            return
        self.update_status.set(f"CamAndroid {release.version} is available (you have {__version__}). "
                               "Update the phone app too: the APK is on the release page.")
        can_install = release.installer is not None and updates.installed_with_setup()
        self.install_btn.config(text="Download and install" if can_install else "Open download page")
        self.install_btn.grid(row=1, column=1, sticky="e", padx=(8, 0), pady=(8, 0))
        self.status.set(f"Update available: CamAndroid {release.version} (see About)")
        if not quiet:
            self.tabs.select(2)

    def install_update(self) -> None:
        release = self.release
        if release is None:
            return
        if release.installer is None or not updates.installed_with_setup():
            webbrowser.open(release.url)
            return
        if self.session and not messagebox.askyesno(
                "Update", "Installing the update stops the webcam and closes CamAndroid. Continue?"):
            return
        self.install_btn.config(state="disabled")
        self.update_progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        asset = release.installer

        def progress(done: int, total: int) -> None:
            self.events.put(("update_progress", 100 * done / total if total else 0))

        def work():
            try:
                self.events.put(("update_ready", updates.download(asset, progress)))
            except updates.UpdateError as e:
                log.error("Update download failed: %s", e)
                self.events.put(("update_failed", str(e)))

        log.info("Downloading %s", asset.url)
        threading.Thread(target=work, name="update-download", daemon=True).start()

    def _run_installer(self, path) -> None:
        log.info("Starting installer %s", path)
        try:
            os.startfile(path)  # noqa: S606 - verified download from our GitHub releases
        except OSError as e:
            messagebox.showerror("Update", f"Could not start the installer: {e}")
            self.install_btn.config(state="normal")
            return
        self.on_close()

    # -- event pump --------------------------------------------------------------------------

    def _pump(self) -> None:
        latest_preview = None
        log_lines: list[tuple[int, str]] = []
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(value)
                elif kind == "error":
                    self.status.set(value)
                    messagebox.showerror("CamAndroid", value)
                elif kind == "preview":
                    latest_preview = value
                elif kind == "log":
                    log_lines.append(value)
                elif kind == "finished":
                    self.session = None
                    self.start_btn.config(text="Start webcam", state="normal")
                    self.res_box.config(state="readonly")
                    self.fps_box.config(state="readonly")
                    self._set_preview(Image.new("RGB", PREVIEW_SIZE, (24, 24, 24)))
                    if not self.status.get().startswith("Error"):
                        self.status.set("Stopped")
                elif kind == "discovered":
                    self.discovered = {d.device_id: d for d in value if d.device_id}
                    self.refresh_devices()
                elif kind == "usb":
                    self.usb_phones = value
                    self.refresh_devices()
                elif kind == "paired":
                    self.status.set(f"Paired with {value}")
                    self.refresh_devices()
                    messagebox.showinfo("CamAndroid", f"Paired with {value}. Select it and press Start webcam.")
                elif kind == "pair_failed":
                    self.status.set("Pairing failed")
                    messagebox.showerror("Pairing failed", value)
                elif kind == "release":
                    self._show_release(*value)
                elif kind == "release_failed":
                    message, quiet = value
                    self.check_btn.config(state="normal")
                    self.update_status.set(f"Could not check for updates: {message}")
                    if not quiet:
                        messagebox.showerror("Updates", f"Could not check for updates: {message}")
                elif kind == "update_progress":
                    self.update_progress["value"] = value
                elif kind == "update_ready":
                    self._run_installer(value)
                    return
                elif kind == "update_failed":
                    self.install_btn.config(state="normal")
                    self.update_progress.grid_remove()
                    messagebox.showerror("Update", value)
        except queue.Empty:
            pass
        if log_lines:
            self._append_log(log_lines)
        if latest_preview is not None:
            self._set_preview(latest_preview)
        self.root.after(50, self._pump)

    def on_close(self) -> None:
        self._closing.set()
        if self.session:
            self.session.stop()
            self.session.join(3)
        if self.browser:
            self.browser.close()
        logging.getLogger("camandroid_desktop").removeHandler(self._log_handler)
        self.root.destroy()


def main() -> int:
    logs.setup()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam" if root.tk.call("tk", "windowingsystem") == "x11" else ttk.Style().theme_use())
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0
