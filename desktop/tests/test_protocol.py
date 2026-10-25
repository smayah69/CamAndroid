import io

import pytest

from camandroid_desktop import protocol as p

# Shared with android/core/src/test/kotlin/com/camandroid/core/ProtocolTest.kt
FP = "ab" * 32
SN = "01" * 16
CN = "02" * 16
CLIENT_ID = "client-0001"
CODE = "ABCD1234EFGH"
TOKEN = "cd" * 32


def test_proofs_match_shared_vectors():
    assert p.pair_proof(CODE, FP, SN, CN, CLIENT_ID) == "b420117ce53300f6481d2f6a876653da0f1df0141dc19bfaff870793af8f7a30"
    assert p.pair_ack(CODE, FP, SN, CN) == "6fbb4e30fd8c577c82860b98ed816790fa07d321aab44965edae04f5c94d936c"
    assert p.auth_proof(TOKEN, FP, SN, CN, CLIENT_ID) == "4404b3e2063f190dafb5e1a8dfc726432bcd687351056d1444f362ae243e821c"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("abcd-1234-efgh", "ABCD1234EFGH"),
        ("OIL0 abcd efgh", "0110ABCDEFGH"),
        ("ABCD-1234-EFG", None),
        ("ABCD-1234-EFGU", None),
    ],
)
def test_normalize_code(raw, expected):
    assert p.normalize_code(raw) == expected


class Pipe:
    def __init__(self, data: bytes):
        self.buf = io.BytesIO(data)

    def recv(self, n):
        return self.buf.read(n)


def test_framing_round_trip():
    data = p.encode_control({"op": "hello", "v": 1}) + p.encode_frame(p.Frame(123, 90, 1, b"\xff\xd8jpeg"))
    pipe = Pipe(data)
    assert p.read_message(pipe) == {"op": "hello", "v": 1}
    frame = p.read_message(pipe)
    assert (frame.timestamp_us, frame.rotation, frame.front_camera, frame.jpeg) == (123, 90, True, b"\xff\xd8jpeg")
    with pytest.raises(ConnectionError):
        p.read_message(pipe)


def test_rejects_oversized_messages():
    import struct

    with pytest.raises(p.ProtocolError):
        p.read_message(Pipe(struct.pack(">BI", p.MSG_JSON, p.MAX_MESSAGE + 1)))
