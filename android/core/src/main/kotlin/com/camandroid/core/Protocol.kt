package com.camandroid.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Wire protocol shared with the desktop client. See docs/PROTOCOL.md for the full specification;
 * any change here must be mirrored in desktop/camandroid_desktop/protocol.py.
 */
object Protocol {
    const val VERSION = 1
    const val DEFAULT_PORT = 4747
    const val NSD_SERVICE_TYPE = "_camandroid._tcp"

    const val MSG_JSON = 1
    const val MSG_FRAME = 2
    const val MAX_MESSAGE = 16 * 1024 * 1024
    const val FRAME_HEADER_SIZE = 12
    const val FLAG_FRONT_CAMERA = 0x1
    const val FLAG_KEY_FRAME = 0x2 // decodable on its own (every JPEG frame, H.264 IDR frames)

    /** Crockford base32: no I, L, O or U, so codes are easy to read aloud and type. */
    const val CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    const val CODE_LENGTH = 12 // 60 bits of entropy
    const val NONCE_BYTES = 16
    const val TOKEN_BYTES = 32

    fun generatePairingCode(random: SecureRandom): String =
        String(CharArray(CODE_LENGTH) { CODE_ALPHABET[random.nextInt(CODE_ALPHABET.length)] })

    fun formatCode(code: String): String = code.chunked(4).joinToString("-")

    /** Returns the canonical code, or null when [input] cannot be a valid code. */
    fun normalizeCode(input: String): String? {
        val sb = StringBuilder()
        for (c in input.uppercase()) {
            when (c) {
                '-', ' ' -> continue
                'O' -> sb.append('0')
                'I', 'L' -> sb.append('1')
                else -> if (c in CODE_ALPHABET) sb.append(c) else return null
            }
        }
        return if (sb.length == CODE_LENGTH) sb.toString() else null
    }

    fun pairProof(code: String, fingerprint: String, serverNonce: String, clientNonce: String, clientId: String) =
        hmacHex(code.toByteArray(), "camandroid-pair-v1|$fingerprint|$serverNonce|$clientNonce|$clientId")

    fun pairAck(code: String, fingerprint: String, serverNonce: String, clientNonce: String) =
        hmacHex(code.toByteArray(), "camandroid-pair-ack-v1|$fingerprint|$serverNonce|$clientNonce")

    fun authProof(token: String, fingerprint: String, serverNonce: String, clientNonce: String, clientId: String) =
        hmacHex(Hex.decode(token), "camandroid-auth-v1|$fingerprint|$serverNonce|$clientNonce|$clientId")

    fun randomHex(random: SecureRandom, bytes: Int): String = Hex.encode(ByteArray(bytes).also(random::nextBytes))

    fun sha256Hex(data: ByteArray): String = Hex.encode(MessageDigest.getInstance("SHA-256").digest(data))

    fun constantTimeEquals(a: String, b: String): Boolean =
        MessageDigest.isEqual(a.toByteArray(), b.toByteArray())

    private fun hmacHex(key: ByteArray, message: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        return Hex.encode(mac.doFinal(message.toByteArray()))
    }
}

object Hex {
    private const val DIGITS = "0123456789abcdef"

    fun encode(bytes: ByteArray): String {
        val out = CharArray(bytes.size * 2)
        bytes.forEachIndexed { i, b ->
            out[i * 2] = DIGITS[(b.toInt() shr 4) and 0xF]
            out[i * 2 + 1] = DIGITS[b.toInt() and 0xF]
        }
        return String(out)
    }

    fun decode(hex: String): ByteArray {
        require(hex.length % 2 == 0) { "odd hex length" }
        return ByteArray(hex.length / 2) { i ->
            val hi = Character.digit(hex[i * 2], 16)
            val lo = Character.digit(hex[i * 2 + 1], 16)
            require(hi >= 0 && lo >= 0) { "invalid hex" }
            ((hi shl 4) or lo).toByte()
        }
    }

    fun isHex(s: String, bytes: Int): Boolean =
        s.length == bytes * 2 && s.all { it in '0'..'9' || it in 'a'..'f' }
}

sealed class Message {
    data class Control(val body: JsonObject) : Message() {
        val op: String? get() = body.string("op")
    }

    class Frame(val timestampUs: Long, val rotation: Int, val flags: Int, val data: ByteArray) : Message()
}

class ProtocolException(val code: String, message: String) : IOException(message)

class MessageReader(input: InputStream) {
    private val data = DataInputStream(input)

    /** Returns null on a clean end of stream. */
    fun read(): Message? {
        val type = data.read()
        if (type < 0) return null
        val length = data.readInt()
        if (length < 0 || length > Protocol.MAX_MESSAGE) throw ProtocolException("protocol", "bad length $length")
        val payload = ByteArray(length)
        data.readFully(payload)
        return when (type) {
            Protocol.MSG_JSON -> {
                val body = try {
                    Json.parseToJsonElement(payload.decodeToString()).jsonObject
                } catch (e: IllegalArgumentException) {
                    throw ProtocolException("protocol", "invalid JSON")
                }
                Message.Control(body)
            }
            Protocol.MSG_FRAME -> {
                if (length < Protocol.FRAME_HEADER_SIZE) throw ProtocolException("protocol", "short frame")
                val header = DataInputStream(payload.inputStream())
                val ts = header.readLong()
                val rotation = header.readUnsignedShort()
                val flags = header.readUnsignedShort()
                Message.Frame(ts, rotation, flags, payload.copyOfRange(Protocol.FRAME_HEADER_SIZE, length))
            }
            else -> throw ProtocolException("protocol", "unknown message type $type")
        }
    }

    fun readControl(): Message.Control = when (val m = read()) {
        null -> throw EOFException("connection closed")
        is Message.Control -> m
        is Message.Frame -> throw ProtocolException("protocol", "expected control message")
    }
}

class MessageWriter(output: OutputStream) {
    private val data = DataOutputStream(BufferedOutputStream(output, 64 * 1024))

    @Synchronized
    fun writeControl(body: JsonObject) {
        val bytes = body.toString().toByteArray()
        data.writeByte(Protocol.MSG_JSON)
        data.writeInt(bytes.size)
        data.write(bytes)
        data.flush()
    }

    @Synchronized
    fun writeFrame(timestampUs: Long, rotation: Int, flags: Int, payload: ByteArray) {
        data.writeByte(Protocol.MSG_FRAME)
        data.writeInt(Protocol.FRAME_HEADER_SIZE + payload.size)
        data.writeLong(timestampUs)
        data.writeShort(rotation)
        data.writeShort(flags)
        data.write(payload)
        data.flush()
    }
}
