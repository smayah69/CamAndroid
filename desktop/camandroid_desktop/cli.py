"""Command-line interface. Run without arguments to open the GUI."""

from __future__ import annotations

import argparse
import getpass
import sys
import time

from . import __version__, adb, protocol
from .client import CamAndroidError, PhoneConnection, StreamSettings
from .session import SessionCallbacks, StreamSession, Target, connect
from .store import DeviceStore
from .video import OutputOptions


def _parse_size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in text.lower().split("x"))
    except ValueError as e:
        raise argparse.ArgumentTypeError("size must look like 1280x720") from e
    return w, h


def _parse_host(text: str, default_port: int) -> tuple[str, int]:
    if text.count(":") == 1:
        host, port = text.split(":")
        return host, int(port)
    return text, default_port


def resolve_target(store: DeviceStore, args) -> Target:
    if args.usb:
        return Target(usb=True, serial=args.serial, port=args.port)
    if args.target:
        dev = store.find(args.target)
        if dev:
            return Target(device=dev)
        host, port = _parse_host(args.target, args.port)
        return Target(host=host, port=port)
    if len(store.devices) == 1:
        return Target(device=next(iter(store.devices.values())))
    if not store.devices:
        raise CamAndroidError("No paired phones. Run 'camandroid pair' first.")
    raise CamAndroidError("Several phones are paired; name one (see 'camandroid devices').")


def cmd_discover(args, store: DeviceStore) -> int:
    from .discovery import discover

    phones = discover(args.timeout)
    if not phones:
        print("No phones found. Is the CamAndroid app running and on the same network?")
        return 1
    for d in phones:
        paired = any(dev.device_id == d.device_id for dev in store.devices.values())
        print(f"{d.name:30} {d.host}:{d.port:<6} id={d.device_id} {'(paired)' if paired else ''}")
    return 0


def cmd_pair(args, store: DeviceStore) -> int:
    if args.usb:
        target = Target(usb=True, serial=args.serial, port=args.port)
    elif args.target:
        host, port = _parse_host(args.target, args.port)
        target = Target(host=host, port=port)
    else:
        from .discovery import discover

        phones = discover(3.0)
        if len(phones) != 1:
            print(f"Found {len(phones)} phones; pass the address shown in the app, "
                  "e.g. 'camandroid pair 192.168.1.20'.")
            return 1
        target = Target(host=phones[0].host, port=phones[0].port)
        print(f"Found {phones[0].label}")
    with connect(store, target) as conn:
        print(f"Phone certificate: {protocol.format_fingerprint(conn.fingerprint)}")
        code = args.code or getpass.getpass("Pairing code shown on the phone (tap 'Pair new computer'): ")
        device = conn.pair(store, code)
    if target.usb:
        device.host, device.port = "", 0
        store.save()
    print(f"Paired with {device.name} (id {device.device_id}).")
    return 0


def cmd_devices(args, store: DeviceStore) -> int:
    if not store.devices:
        print("No paired phones.")
    for d in store.devices.values():
        where = f"{d.host}:{d.port}" if d.host else "-"
        print(f"{d.name:30} id={d.device_id}  last={where}  cert={protocol.format_fingerprint(d.fingerprint)}")
    return 0


def cmd_forget(args, store: DeviceStore) -> int:
    dev = store.find(args.device)
    if not dev:
        print(f"No paired phone matches {args.device!r}.")
        return 1
    store.remove(dev.fingerprint)
    print(f"Forgot {dev.name}. Also remove this computer under 'Paired computers' in the app.")
    return 0


def cmd_stream(args, store: DeviceStore) -> int:
    target = resolve_target(store, args)
    width, height = args.size
    settings = StreamSettings(width=width, height=height, fps=args.fps, quality=args.quality,
                              camera=args.camera, torch=args.torch)
    output = OutputOptions(width=width, height=height, rotate=args.rotate, mirror=args.mirror, fill=args.fill)
    errors: list[str] = []
    session = StreamSession(
        store, target, settings, output,
        SessionCallbacks(on_status=lambda s: print(s, flush=True), on_error=errors.append),
        virtual_camera=not args.no_vcam, backend=args.backend, device=args.device,
    )
    print(f"Connecting to {target.describe()} … (Ctrl+C to stop)")
    session.start()
    try:
        while session.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("Stopping…")
        session.stop()
        session.join(5)
    for e in errors:
        print(f"Error: {e}", file=sys.stderr)
    return 1 if errors else 0


def cmd_probe(args, store: DeviceStore) -> int:
    """Connect and print what the phone reports, without streaming (diagnostics)."""
    host, port = _parse_host(args.target, args.port)
    with PhoneConnection(host, port) as conn:
        print(f"TLS {conn.sock.version()}  cipher {conn.sock.cipher()[0]}")
        print(f"Certificate fingerprint: {conn.fingerprint}")
        dev = store.get(conn.fingerprint)
        print(f"Paired: {'yes (' + dev.name + ')' if dev else 'no'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="camandroid", description="Use your Android phone as a webcam.")
    parser.add_argument("--version", action="version", version=f"camandroid {__version__}")
    sub = parser.add_subparsers(dest="command")

    def add_connection(p: argparse.ArgumentParser, target_help: str) -> None:
        p.add_argument("target", nargs="?", help=target_help)
        p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
        p.add_argument("--usb", action="store_true", help="connect over USB using adb port forwarding")
        p.add_argument("--serial", help="adb serial when several devices are connected")

    p = sub.add_parser("discover", help="list phones running CamAndroid on the network")
    p.add_argument("-t", "--timeout", type=float, default=3.0)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("pair", help="pair with a phone using the code shown in the app")
    add_connection(p, "phone address (host or host:port); discovered automatically if omitted")
    p.add_argument("--code", help="pairing code (prompted if omitted)")
    p.set_defaults(func=cmd_pair)

    p = sub.add_parser("devices", help="list paired phones")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("forget", help="remove a paired phone")
    p.add_argument("device", help="device name or id")
    p.set_defaults(func=cmd_forget)

    p = sub.add_parser("stream", help="stream a paired phone to the virtual webcam")
    add_connection(p, "paired device name/id or address; optional if only one phone is paired")
    p.add_argument("--size", type=_parse_size, default=(1280, 720), help="output size (default 1280x720)")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--quality", type=int, default=80, help="JPEG quality 30-100")
    p.add_argument("--camera", choices=["back", "front"], default="back")
    p.add_argument("--torch", action="store_true")
    p.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0, help="extra clockwise rotation")
    p.add_argument("--mirror", action="store_true")
    p.add_argument("--fill", action="store_true", help="crop to fill the frame instead of letterboxing")
    p.add_argument("--backend", help="pyvirtualcam backend (obs, unitycapture, v4l2loopback, ...)")
    p.add_argument("--device", help="virtual camera device, e.g. /dev/video10")
    p.add_argument("--no-vcam", action="store_true", help="receive and decode only (testing)")
    p.set_defaults(func=cmd_stream)

    p = sub.add_parser("probe", help="show TLS details and pairing status of a phone")
    p.add_argument("target")
    p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("gui", help="open the graphical interface (default)")
    p.set_defaults(func=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "func", None) is None:
        from .gui import main as gui_main

        return gui_main()
    store = DeviceStore()
    try:
        return args.func(args, store)
    except (CamAndroidError, adb.AdbError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
