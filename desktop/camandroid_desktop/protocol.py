"""Wire protocol shared with the Android app (see docs/PROTOCOL.md).

Mirrors android/core/src/main/kotlin/com/camandroid/core/Protocol.kt; keep them in sync.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import struct
from dataclasses import dataclass
from typing import Protocol as TypingProtocol

VERSION = 1
DEFAULT_PORT = 4747
SERVICE_TYPE = "_camandroid._tcp.local."

MSG_JSON = 1
MSG_FRAME = 2
MAX_MESSAGE = 16 * 1024 * 1024
FRAME_HEADER = struct.Struct(">qHH")  # timestamp_us, rotation, flags
FLAG_FRONT_CAMERA = 0x1
FLAG_KEY_FRAME = 0x2  # decodable on its own: every JPEG frame, H.264 IDR frames (with SPS/PPS)

CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 12
NONCE_BYTES = 16


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Frame:
    timestamp_us: int
    rotation: int
    flags: int
    data: bytes  # one JPEG image or one H.264 access unit (Annex-B)

    @property
    def front_camera(self) -> bool:
        return bool(self.flags & FLAG_FRONT_CAMERA)

    @property
    def key_frame(self) -> bool:
        return bool(self.flags & FLAG_KEY_FRAME)


class _Stream(TypingProtocol):
    def recv(self, n: int) -> bytes: ...
    def sendall(self, data: bytes) -> None: ...


def normalize_code(text: str) -> str | None:
    """Canonical form of a pairing code, or None if it cannot be valid."""
    out = []
    for c in text.upper():
        if c in "- ":
            continue
        c = {"O": "0", "I": "1", "L": "1"}.get(c, c)
        if c not in CODE_ALPHABET:
            return None
        out.append(c)
    return "".join(out) if len(out) == CODE_LENGTH else None


def format_code(code: str) -> str:
    return "-".join(code[i : i + 4] for i in range(0, len(code), 4))


def _hmac(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def pair_proof(code: str, fingerprint: str, server_nonce: str, client_nonce: str, client_id: str) -> str:
    return _hmac(code.encode(), f"camandroid-pair-v1|{fingerprint}|{server_nonce}|{client_nonce}|{client_id}")


def pair_ack(code: str, fingerprint: str, server_nonce: str, client_nonce: str) -> str:
    return _hmac(code.encode(), f"camandroid-pair-ack-v1|{fingerprint}|{server_nonce}|{client_nonce}")


def auth_proof(token: str, fingerprint: str, server_nonce: str, client_nonce: str, client_id: str) -> str:
    return _hmac(bytes.fromhex(token), f"camandroid-auth-v1|{fingerprint}|{server_nonce}|{client_nonce}|{client_id}")


def new_nonce() -> str:
    return secrets.token_hex(NONCE_BYTES)


def fingerprint_of(cert_der: bytes) -> str:
    return hashlib.sha256(cert_der).hexdigest()


def format_fingerprint(fp: str) -> str:
    """Short, human-comparable form, e.g. 'AB12 CD34 EF56 7890'."""
    return " ".join(fp[i : i + 4] for i in range(0, 16, 4)).upper()


def _recv_exact(sock: _Stream, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed by phone")
        buf += chunk
    return bytes(buf)


def read_message(sock: _Stream) -> dict | Frame:
    msg_type, length = struct.unpack(">BI", _recv_exact(sock, 5))
    if length > MAX_MESSAGE:
        raise ProtocolError("protocol", f"message too large ({length} bytes)")
    payload = _recv_exact(sock, length)
    if msg_type == MSG_JSON:
        try:
            body = json.loads(payload)
        except ValueError as e:
            raise ProtocolError("protocol", "invalid JSON") from e
        if not isinstance(body, dict):
            raise ProtocolError("protocol", "control message must be an object")
        return body
    if msg_type == MSG_FRAME:
        if length < FRAME_HEADER.size:
            raise ProtocolError("protocol", "short frame")
        ts, rotation, flags = FRAME_HEADER.unpack_from(payload)
        return Frame(ts, rotation, flags, payload[FRAME_HEADER.size :])
    raise ProtocolError("protocol", f"unknown message type {msg_type}")


def encode_control(body: dict) -> bytes:
    data = json.dumps(body, separators=(",", ":")).encode()
    return struct.pack(">BI", MSG_JSON, len(data)) + data


def encode_frame(frame: Frame) -> bytes:
    payload = FRAME_HEADER.pack(frame.timestamp_us, frame.rotation, frame.flags) + frame.data
    return struct.pack(">BI", MSG_FRAME, len(payload)) + payload


def write_control(sock: _Stream, body: dict) -> None:
    sock.sendall(encode_control(body))
