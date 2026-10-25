"""Python re-implementation of the phone side, used to test the desktop client in isolation."""

from __future__ import annotations

import datetime
import io
import secrets
import socket
import ssl
import tempfile
import threading
import time
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from PIL import Image

from camandroid_desktop import protocol as p


def make_jpeg(width: int, height: int, color=(200, 80, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "JPEG")
    return buf.getvalue()


class MockPhone:
    def __init__(self, rotation: int = 0):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CamAndroid")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
            .sign(key, hashes.SHA256())
        )
        self.fingerprint = p.fingerprint_of(cert.public_bytes(serialization.Encoding.DER))
        self._tmp = tempfile.TemporaryDirectory()
        cert_path, key_path = Path(self._tmp.name, "c.pem"), Path(self._tmp.name, "k.pem")
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert_path, key_path)
        self.code: str | None = None
        self.clients: dict[str, str] = {}
        self.rotation = rotation
        self.started_with: dict | None = None
        self.updates: list[dict] = []
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = self._server.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def new_code(self) -> str:
        self.code = "".join(secrets.choice(p.CODE_ALPHABET) for _ in range(p.CODE_LENGTH))
        return self.code

    def close(self) -> None:
        self._stop.set()
        self._server.close()
        self._tmp.cleanup()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                raw, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(raw,), daemon=True).start()

    def _session(self, raw: socket.socket) -> None:
        try:
            with self.ctx.wrap_socket(raw, server_side=True) as s:
                self._handle(s)
        except (OSError, ConnectionError, ssl.SSLError):
            pass

    def _error(self, s, code: str) -> None:
        p.write_control(s, {"op": "error", "code": code, "message": code})

    def _handle(self, s) -> None:
        hello = p.read_message(s)
        client_id = hello["client_id"]
        nonce = p.new_nonce()
        p.write_control(s, {"op": "hello", "v": 1, "device_name": "Mock Phone", "device_id": self.fingerprint[:16],
                            "nonce": nonce, "pairing": self.code is not None})
        req = p.read_message(s)
        cn = req["client_nonce"]
        if req["op"] == "pair":
            code, self.code = self.code, None
            if code is None:
                return self._error(s, "no_pairing")
            if req["proof"] != p.pair_proof(code, self.fingerprint, nonce, cn, client_id):
                return self._error(s, "bad_code")
            token = secrets.token_hex(32)
            self.clients[client_id] = token
            p.write_control(s, {"op": "paired", "token": token, "proof": p.pair_ack(code, self.fingerprint, nonce, cn)})
        elif req["op"] == "auth":
            token = self.clients.get(client_id)
            if token is None:
                return self._error(s, "unknown_client")
            if req["proof"] != p.auth_proof(token, self.fingerprint, nonce, cn, client_id):
                return self._error(s, "bad_proof")
        p.write_control(s, {"op": "auth_ok", "cameras": ["back", "front"]})
        s.settimeout(0.02)
        jpeg = None
        while not self._stop.is_set():
            try:
                msg = p.read_message(s)
                if msg["op"] == "start":
                    self.started_with = msg
                    jpeg = make_jpeg(msg["width"], msg["height"])
                    p.write_control(s, {"op": "started"})
                elif msg["op"] == "update":
                    self.updates.append(msg)
            except TimeoutError:
                pass
            if jpeg:
                s.sendall(p.encode_frame(p.Frame(int(time.time() * 1e6), self.rotation, 0, jpeg)))
