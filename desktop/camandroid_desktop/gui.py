"""Tkinter GUI: discover and pair phones, then stream one to the virtual webcam."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from . import __version__, protocol
from .client import StreamSettings
from .session import SessionCallbacks, StreamSession, Target, connect
from .store import DeviceStore
from .video import OutputOptions

RESOLUTIONS = ["640x480", "1280x720", "1920x1080"]
FPS_CHOICES = ["15", "24", "30", "60"]
ROTATIONS = ["0", "90", "180", "270"]
PREVIEW_SIZE = (384, 216)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.store = DeviceStore()
        self.events: queue.Queue = queue.Queue()
        self.session: StreamSession | None = None
        self.discovered: dict[str, object] = {}  # device_id -> DiscoveredPhone
        self.browser = None
        self._preview_photo = None

        root.title(f"CamAndroid {__version__}")
        root.minsize(760, 460)
        self._build()
        self._start_discovery()
        self.refresh_devices()
        root.after(50, self._pump)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # -- layout ------------------------------------------------------------------------------

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(main, text="Phones", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, columns=("address", "status"), show="tree headings", height=8)
        self.tree.heading("#0", text="Name")
        self.tree.heading("address", text="Address")
        self.tree.heading("status", text="Status")
        self.tree.column("#0", width=170)
        self.tree.column("address", width=140)
        self.tree.column("status", width=80)
        self.tree.grid(row=0, column=0, columnspan=3, sticky="nsew")

        ttk.Button(left, text="Pair…", command=self.pair_selected).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(left, text="Pair by address…", command=self.pair_by_address).grid(
            row=1, column=1, sticky="ew", pady=(8, 0), padx=4)
        ttk.Button(left, text="Forget", command=self.forget_selected).grid(row=1, column=2, sticky="ew", pady=(8, 0))
        self.usb = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="Connect over USB (adb) instead of Wi-Fi", variable=self.usb).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(left, text="On the phone: open CamAndroid, tap Start, then 'Pair new computer'.",
                  foreground="gray", wraplength=360).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        right = ttk.LabelFrame(main, text="Webcam", padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)

        self.resolution = tk.StringVar(value="1280x720")
        self.fps = tk.StringVar(value="30")
        self.quality = tk.IntVar(value=80)
        self.camera = tk.StringVar(value="back")
        self.rotate = tk.StringVar(value="0")
        self.mirror = tk.BooleanVar(value=False)
        self.fill = tk.BooleanVar(value=False)
        self.torch = tk.BooleanVar(value=False)

        row = 0
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
        blank = Image.new("RGB", PREVIEW_SIZE, (24, 24, 24))
        self._set_preview(blank)

        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w", padding=4).pack(
            fill="x", side="bottom")

    # -- devices -----------------------------------------------------------------------------

    def _start_discovery(self) -> None:
        try:
            from .discovery import PhoneBrowser

            self.browser = PhoneBrowser(on_change=lambda phones: self.events.put(("discovered", phones)))
        except Exception as e:  # noqa: BLE001 - discovery is optional (e.g. firewall, no zeroconf)
            self.status.set(f"Network discovery unavailable: {e}. Use 'Pair by address'.")

    def refresh_devices(self) -> None:
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        seen = set()
        for dev in self.store.devices.values():
            found = self.discovered.get(dev.device_id)
            address = f"{found.host}:{found.port}" if found else (f"{dev.host}:{dev.port}" if dev.host else "")
            status = "Online" if found else "Paired"
            self.tree.insert("", "end", iid=dev.device_id, text=dev.name, values=(address, status))
            seen.add(dev.device_id)
        for device_id, found in self.discovered.items():
            if device_id not in seen:
                self.tree.insert("", "end", iid=f"new:{device_id}", text=found.name,
                                 values=(f"{found.host}:{found.port}", "Not paired"))
        for iid in selected:
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
        children = self.tree.get_children()
        if not self.tree.selection() and children:
            self.tree.selection_set(children[0])

    def _selected(self) -> tuple[str | None, bool]:
        sel = self.tree.selection()
        if not sel:
            return None, False
        iid = sel[0]
        return (iid[4:], False) if iid.startswith("new:") else (iid, True)

    def pair_selected(self) -> None:
        if self.usb.get():
            self._pair(Target(usb=True))
            return
        device_id, _paired = self._selected()
        found = self.discovered.get(device_id) if device_id else None
        if not found:
            self.pair_by_address()
            return
        self._pair(Target(host=found.host, port=found.port))

    def pair_by_address(self) -> None:
        text = simpledialog.askstring("Pair by address", "Phone address shown in the app (e.g. 192.168.1.20):",
                                      parent=self.root)
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

        def work():
            try:
                with connect(self.store, target) as conn:
                    device = conn.pair(self.store, code)
                if target.usb:
                    device.host, device.port = "", 0
                    self.store.save()
                self.events.put(("paired", device.name))
            except Exception as e:  # noqa: BLE001
                self.events.put(("pair_failed", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def forget_selected(self) -> None:
        device_id, paired = self._selected()
        dev = self.store.find(device_id) if (device_id and paired) else None
        if not dev:
            return
        if messagebox.askyesno("Forget phone", f"Forget {dev.name}? You will need to pair again."):
            self.store.remove(dev.fingerprint)
            self.refresh_devices()

    # -- streaming ---------------------------------------------------------------------------

    def toggle_stream(self) -> None:
        if self.session:
            self.session.stop()
            self.start_btn.config(state="disabled")
            return
        if self.usb.get():
            target = Target(usb=True)
            # Over USB the phone is identified by its certificate after connecting.
        else:
            device_id, paired = self._selected()
            dev = self.store.find(device_id) if (device_id and paired) else None
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

    # -- event pump --------------------------------------------------------------------------

    def _pump(self) -> None:
        latest_preview = None
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
                elif kind == "paired":
                    self.status.set(f"Paired with {value}")
                    self.refresh_devices()
                    messagebox.showinfo("CamAndroid", f"Paired with {value}. Select it and press Start webcam.")
                elif kind == "pair_failed":
                    self.status.set("Pairing failed")
                    messagebox.showerror("Pairing failed", value)
        except queue.Empty:
            pass
        if latest_preview is not None:
            self._set_preview(latest_preview)
        self.root.after(50, self._pump)

    def on_close(self) -> None:
        if self.session:
            self.session.stop()
            self.session.join(3)
        if self.browser:
            self.browser.close()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam" if root.tk.call("tk", "windowingsystem") == "x11" else ttk.Style().theme_use())
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0
