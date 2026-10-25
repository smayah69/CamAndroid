"""TLS client for the CamAndroid phone app: pairing, authentication and frame streaming."""

from __future__ import annotations

import hmac
import socket
import ssl
from dataclasses import asdict, dataclass

from . import protocol as p
from .store import DeviceStore, PairedDevice


class CamAndroidError(Exception):
    """Base class for errors worth showing to the user as-is."""


class RemoteError(CamAndroidError):
    def __init__(self, code: str, message: str):
        super().__init__(message or code)
        self.code = code


class NotPairedError(CamAndroidError):
    pass


class FingerprintMismatchError(CamAndroidError):
    pass


@dataclass
class StreamSettings:
    width: int = 1280
    height: int = 720
    fps: int = 30
    quality: int = 80
    camera: str = "back"
    torch: bool = False


class PhoneConnection:
    """One TLS connection to a phone.

    The phone uses a self-signed certificate, so the TLS layer does not validate it; instead the
    certificate's SHA-256 fingerprint is bound into the pairing proof and pinned afterwards.
    """

    def __init__(self, host: str, port: int = p.DEFAULT_PORT, timeout: float = 10.0):
        self.host, self.port = host, port
        raw = socket.create_connection((host, port), timeout=timeout)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            self.sock = ctx.wrap_socket(raw)
        except (ssl.SSLError, OSError):
            raw.close()
            raise
        der = self.sock.getpeercert(binary_form=True)
        if not der:
            self.close()
            raise CamAndroidError("phone did not present a certificate")
        self.fingerprint = p.fingerprint_of(der)
        self.device_name = ""
        self.pairing_open = False
        self.cameras: list[str] = []
        self._server_nonce = ""

    # -- handshake ---------------------------------------------------------------------------

    def _hello(self, store: DeviceStore) -> None:
        p.write_control(self.sock, {
            "op": "hello", "v": p.VERSION,
            "client_id": store.client_id, "client_name": store.client_name,
        })
        reply = self._expect_control()
        if reply.get("op") != "hello" or reply.get("v") != p.VERSION:
            raise RemoteError("version", "phone app uses an incompatible protocol version")
        self.device_name = str(reply.get("device_name", "Android phone"))[:64]
        self.pairing_open = bool(reply.get("pairing"))
        nonce = reply.get("nonce", "")
        if not isinstance(nonce, str) or len(nonce) != 2 * p.NONCE_BYTES:
            raise RemoteError("protocol", "invalid server nonce")
        self._server_nonce = nonce

    def pair(self, store: DeviceStore, code: str) -> PairedDevice:
        normalized = p.normalize_code(code)
        if normalized is None:
            raise CamAndroidError("pairing codes have 12 characters, like ABCD-1234-EFGH")
        self._hello(store)
        client_nonce = p.new_nonce()
        p.write_control(self.sock, {
            "op": "pair", "client_nonce": client_nonce,
            "proof": p.pair_proof(normalized, self.fingerprint, self._server_nonce, client_nonce, store.client_id),
        })
        reply = self._expect_control()
        expected_ack = p.pair_ack(normalized, self.fingerprint, self._server_nonce, client_nonce)
        if reply.get("op") != "paired" or not _ct_eq(str(reply.get("proof", "")), expected_ack):
            raise CamAndroidError("phone failed to prove it knows the pairing code (possible interception)")
        token = str(reply.get("token", ""))
        if len(token) != 64:
            raise RemoteError("protocol", "invalid token")
        self._expect_auth_ok()
        device = PairedDevice(self.fingerprint, self.device_name, token, self.host, self.port)
        store.add(device)
        return device

    def authenticate(self, store: DeviceStore, expected: PairedDevice | None = None) -> PairedDevice:
        device = store.get(self.fingerprint)
        if expected is not None and expected.fingerprint != self.fingerprint:
            raise FingerprintMismatchError(
                f"{self.host} presented certificate {p.format_fingerprint(self.fingerprint)} but "
                f"'{expected.name}' is {p.format_fingerprint(expected.fingerprint)}. If you reinstalled "
                "the app, forget the device and pair again; otherwise someone may be intercepting traffic."
            )
        if device is None:
            raise NotPairedError(f"the phone at {self.host}:{self.port} is not paired with this computer yet")
        self._hello(store)
        client_nonce = p.new_nonce()
        p.write_control(self.sock, {
            "op": "auth", "client_nonce": client_nonce,
            "proof": p.auth_proof(device.token, self.fingerprint, self._server_nonce, client_nonce, store.client_id),
        })
        self._expect_auth_ok()
        return device

    def _expect_auth_ok(self) -> None:
        reply = self._expect_control()
        if reply.get("op") != "auth_ok":
            raise RemoteError("protocol", f"unexpected reply {reply.get('op')!r}")
        self.cameras = [c for c in reply.get("cameras", []) if isinstance(c, str)]

    def _expect_control(self) -> dict:
        msg = p.read_message(self.sock)
        if not isinstance(msg, dict):
            raise RemoteError("protocol", "expected a control message")
        if msg.get("op") == "error":
            raise RemoteError(str(msg.get("code", "error")), str(msg.get("message", "")))
        return msg

    # -- streaming ---------------------------------------------------------------------------

    def start(self, settings: StreamSettings) -> None:
        p.write_control(self.sock, {"op": "start", **asdict(settings)})

    def update(self, **changes) -> None:
        p.write_control(self.sock, {"op": "update", **changes})

    def stop(self) -> None:
        p.write_control(self.sock, {"op": "stop"})

    def read(self) -> dict | p.Frame:
        msg = p.read_message(self.sock)
        if isinstance(msg, dict) and msg.get("op") == "error":
            raise RemoteError(str(msg.get("code", "error")), str(msg.get("message", "")))
        return msg

    def settimeout(self, timeout: float | None) -> None:
        self.sock.settimeout(timeout)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self) -> PhoneConnection:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _ct_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
