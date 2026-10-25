package com.camandroid.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.security.SecureRandom
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.X509TrustManager

/** Minimal Kotlin implementation of the desktop side, for server tests. */
class TestClient(port: Int, private val clientId: String = "test-client-1") : AutoCloseable {
    private val socket: SSLSocket
    val reader: MessageReader
    val writer: MessageWriter
    val fingerprint: String

    init {
        val trustAll = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        val ctx = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trustAll), SecureRandom()) }
        socket = ctx.socketFactory.createSocket("127.0.0.1", port) as SSLSocket
        socket.soTimeout = 10_000
        socket.startHandshake()
        fingerprint = Protocol.sha256Hex(socket.session.peerCertificates[0].encoded)
        reader = MessageReader(socket.inputStream)
        writer = MessageWriter(socket.outputStream)
    }

    fun hello(): JsonObject {
        writer.writeControl(buildJsonObject {
            put("op", "hello")
            put("v", Protocol.VERSION)
            put("client_id", clientId)
            put("client_name", "Test PC")
        })
        return reader.readControl().body
    }

    fun pair(code: String): JsonObject {
        val serverNonce = hello().string("nonce")!!
        val clientNonce = Protocol.randomHex(SecureRandom(), Protocol.NONCE_BYTES)
        writer.writeControl(buildJsonObject {
            put("op", "pair")
            put("client_nonce", clientNonce)
            put("proof", Protocol.pairProof(code, fingerprint, serverNonce, clientNonce, clientId))
        })
        val reply = reader.readControl().body
        if (reply.string("op") == "paired") {
            check(reply.string("proof") == Protocol.pairAck(code, fingerprint, serverNonce, clientNonce)) { "bad ack" }
        }
        return reply
    }

    fun auth(token: String): JsonObject {
        val serverNonce = hello().string("nonce")!!
        val clientNonce = Protocol.randomHex(SecureRandom(), Protocol.NONCE_BYTES)
        writer.writeControl(buildJsonObject {
            put("op", "auth")
            put("client_nonce", clientNonce)
            put("proof", Protocol.authProof(token, fingerprint, serverNonce, clientNonce, clientId))
        })
        return reader.readControl().body
    }

    override fun close() = socket.close()
}
