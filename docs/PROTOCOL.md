# CamAndroid protocol (v1)

The phone is the **server** (TCP port 4747, TLS), the computer is the **client**. Wi-Fi and USB use the
same protocol; over USB the desktop app runs `adb forward tcp:47470 tcp:4747` and connects to
`127.0.0.1:47470`.

Reference implementations:

- Server: `android/core/src/main/kotlin/com/camandroid/core/` (`Protocol.kt`, `CamServer.kt`)
- Client: `desktop/camandroid_desktop/` (`protocol.py`, `client.py`)

Shared test vectors live in `ProtocolTest.kt` and `test_protocol.py`, and CI runs the Python client
against the Kotlin server (`interop` job).

## Discovery

The phone advertises DNS-SD service `_camandroid._tcp` with TXT records `id` (first 16 hex chars of
the certificate fingerprint) and `v` (protocol version). Discovery only provides convenience; it
doesn't establish trust in anything.

## Transport and identity

- TLS 1.2 or 1.3. The phone has a long-lived EC P-256 key and a self-signed X.509 certificate,
  generated on first start. The private key is encrypted at rest with an AES-256-GCM key held in the
  Android Keystore.
- The client does not validate the certificate chain. It computes
  `fingerprint = hex(SHA-256(certificate DER))` and **binds it into the pairing proof**, then pins it.

## Framing

Every message is `type (u8) | length (u32 BE) | payload`. The maximum length is 16 MiB.

| type | payload |
|------|---------|
| 1    | UTF-8 JSON object (control message, field `op`) |
| 2    | video frame: `timestamp_us (i64 BE) | rotation (u16 BE) | flags (u16 BE) | data` |

`rotation` is the clockwise rotation (0/90/180/270) the client applies to show the image upright.
Flags: bit 0 = front camera, bit 1 = key frame (decodable on its own).

`data` is one JPEG image, or one H.264 access unit in Annex-B format (start codes, no B-frames).
H.264 key frames are IDR frames preceded by their SPS and PPS, so a decoder can start at any key
frame. Every JPEG frame is a key frame.

## Handshake

```
C → S  {"op":"hello","v":1,"client_id":"<8-64 chars>","client_name":"<display name>"}
S → C  {"op":"hello","v":1,"device_name":"…","device_id":"…","nonce":"<32 hex>","pairing":bool}
```

Then the client sends either `pair` (first time) or `auth` (afterwards).

### Pairing

The user taps **Pair new computer** on the phone, which shows a one-time code: 12 Crockford-base32
characters (60 bits), valid for 5 minutes and **for one attempt**. A wrong guess invalidates it.

```
proof = HMAC-SHA256(key = code, "camandroid-pair-v1|" fp "|" server_nonce "|" client_nonce "|" client_id)
C → S  {"op":"pair","client_nonce":"<32 hex>","proof":"<hex>"}
S → C  {"op":"paired","token":"<64 hex>","proof": HMAC-SHA256(code, "camandroid-pair-ack-v1|" fp "|" sn "|" cn)}
S → C  {"op":"auth_ok","cameras":["back","front"],"codecs":["h264","jpeg"]}
```

`codecs` lists what the phone can encode, most preferred first (absent in older apps: JPEG only).

The client verifies the ack proof before it stores the token, and it stores the token together with the pinned
fingerprint.

### Authentication (paired clients)

```
proof = HMAC-SHA256(key = token bytes, "camandroid-auth-v1|" fp "|" server_nonce "|" client_nonce "|" client_id)
C → S  {"op":"auth","client_nonce":"<32 hex>","proof":"<hex>"}
S → C  {"op":"auth_ok","cameras":[…]}
```

Before it sends `auth`, the client checks that the presented fingerprint matches the one it pinned.

### Errors

`{"op":"error","code":…, "message":…}`, followed by connection close. Codes: `protocol`, `version`,
`no_pairing`, `bad_code`, `unknown_client`, `bad_proof`, `busy` (another computer is streaming).

## Streaming

```
C → S  {"op":"start","width":1280,"height":720,"fps":60,"quality":80,"camera":"back","torch":false,
        "codecs":["h264","jpeg"]}
S → C  {"op":"started",…,"codec":"h264"}   then type-2 frames
C → S  {"op":"update", any subset of the start fields}   → {"op":"updated",…}
C → S  {"op":"stop"}                                     → {"op":"stopped"}
C → S  {"op":"ping"}                                     → {"op":"pong"}
```

The phone uses the first entry of the client's `codecs` that it supports, and JPEG when the client
sends none (older desktop apps). `quality` (30-100) is the JPEG quality, or sets the H.264 bitrate
(about 6 Mbit/s for 720p60 at 80). The camera runs at a fixed frame rate (for example [60, 60]) when
it supports one, so it does not slow down in dim light.

A congested link drops frames instead of adding latency: a key frame replaces every frame still
queued, and when about a quarter of a second of H.264 frames is waiting, the phone drops them and
asks its encoder for a new key frame.

## Security notes

| Threat | Mitigation |
|---|---|
| Passive sniffing on Wi-Fi | All traffic is TLS. |
| Random LAN host connecting | Requires a stored 256-bit token, or the on-screen pairing code. |
| Online code guessing | One attempt per code, 60-bit code, 5-minute expiry, maximum of 4 connections in handshake. |
| Active MITM during pairing | The proof covers the fingerprint the client actually saw. A MITM with its own certificate would need to recover the 60-bit code offline before the code expires. It also can't forge the ack, because that needs the code. |
| Active MITM after pairing | Certificate pinning. A changed fingerprint is a hard error. |
| Token theft from phone backups | `allowBackup=false` and data-extraction rules exclude everything. Secrets are Keystore-encrypted. |
| Token theft on the PC | `devices.json` is created with mode 0600 (POSIX). On Windows it lives in the user's `%APPDATA%`. |

Known limitations: the pairing code is a short shared secret, not a PAKE, so the protection against
an active attacker during pairing rests on the code's entropy and its short lifetime.
