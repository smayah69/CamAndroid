package com.camandroid.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.add
import java.io.Closeable
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.security.SecureRandom
import java.util.concurrent.Semaphore
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.locks.ReentrantLock
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket
import kotlin.concurrent.thread
import kotlin.concurrent.withLock

/**
 * TLS server that authenticates desktop clients (pairing code or stored token) and streams frames
 * from a [FrameSource] to at most one client at a time.
 */
class CamServer(
    private val identity: TlsIdentity,
    private val clients: ClientStore,
    private val pairing: PairingManager,
    private val source: FrameSource,
    private val deviceName: String,
    private val listener: Listener = object : Listener {},
    private val random: SecureRandom = SecureRandom(),
) : Closeable {

    interface Listener {
        fun onPaired(client: PairedClient) {}
        fun onPairingFailed(reason: String) {}
        fun onSessionStarted(clientName: String) {}
        fun onStreaming(clientName: String, config: StreamConfig) {}
        fun onStreamStopped(clientName: String) {}
        fun onSessionEnded(clientName: String) {}
    }

    @Volatile private var serverSocket: SSLServerSocket? = null
    private val active = AtomicReference<Session?>(null)
    private val handshakeSlots = Semaphore(MAX_PENDING_CONNECTIONS)

    val port: Int get() = serverSocket?.localPort ?: -1

    /** Binds and starts accepting connections. Returns the bound port (useful when [port] is 0). */
    fun start(port: Int = Protocol.DEFAULT_PORT, bindAddress: InetAddress? = null): Int {
        val socket = identity.sslContext().serverSocketFactory.createServerSocket() as SSLServerSocket
        socket.reuseAddress = true
        socket.bind(InetSocketAddress(bindAddress, port), 8)
        socket.enabledProtocols = socket.supportedProtocols.filter { it == "TLSv1.3" || it == "TLSv1.2" }.toTypedArray()
        serverSocket = socket
        thread(name = "camserver-accept", isDaemon = true) { acceptLoop(socket) }
        return socket.localPort
    }

    override fun close() {
        serverSocket?.close()
        serverSocket = null
        active.get()?.close()
    }

    /** Drops the currently connected client, if any. */
    fun disconnectClient() {
        active.get()?.close()
    }

    private fun acceptLoop(server: SSLServerSocket) {
        while (!server.isClosed) {
            val socket = try {
                server.accept() as SSLSocket
            } catch (e: IOException) {
                break
            }
            if (!handshakeSlots.tryAcquire()) {
                socket.close()
                continue
            }
            thread(name = "camserver-session", isDaemon = true) {
                Session(socket).run()
            }
        }
    }

    private inner class Session(private val socket: SSLSocket) : Closeable {
        private val slot = FrameSlot()
        private var clientName = "unknown"
        private var streaming = false
        private var config = StreamConfig()

        fun run() {
            var authenticated = false
            var registered = false
            try {
                socket.soTimeout = HANDSHAKE_TIMEOUT_MS
                socket.tcpNoDelay = true
                socket.startHandshake()
                val reader = MessageReader(socket.inputStream)
                val writer = MessageWriter(socket.outputStream)
                authenticated = authenticate(reader, writer)
                handshakeSlots.release()
                if (!authenticated) return

                if (!active.compareAndSet(null, this)) {
                    writer.writeControl(error("busy", "another computer is already connected"))
                    return
                }
                registered = true
                socket.soTimeout = 0
                writer.writeControl(buildJsonObject {
                    put("op", "auth_ok")
                    putJsonArray("cameras") { source.cameras().forEach { add(it) } }
                })
                listener.onSessionStarted(clientName)
                val sender = thread(name = "camserver-sender", isDaemon = true) { sendLoop(writer) }
                controlLoop(reader, writer)
                sender.interrupt()
            } catch (e: ProtocolException) {
                tryWrite { MessageWriter(socket.outputStream).writeControl(error(e.code, e.message ?: "")) }
            } catch (e: IOException) {
                // Disconnects and TLS failures end the session; nothing else to do.
            } finally {
                if (!authenticated) handshakeSlots.release()
                if (registered) {
                    if (streaming) source.stop()
                    active.compareAndSet(this, null)
                    listener.onSessionEnded(clientName)
                }
                close()
            }
        }

        private fun authenticate(reader: MessageReader, writer: MessageWriter): Boolean {
            val hello = reader.readControl()
            if (hello.op != "hello") throw ProtocolException("protocol", "expected hello")
            if (hello.body.int("v") != Protocol.VERSION) throw ProtocolException("version", "unsupported protocol version")
            val clientId = hello.body.string("client_id")?.takeIf { it.length in 8..64 }
                ?: throw ProtocolException("protocol", "invalid client_id")
            clientName = sanitizeName(hello.body.string("client_name"))

            val serverNonce = Protocol.randomHex(random, Protocol.NONCE_BYTES)
            writer.writeControl(buildJsonObject {
                put("op", "hello")
                put("v", Protocol.VERSION)
                put("device_name", deviceName)
                put("device_id", identity.deviceId)
                put("nonce", serverNonce)
                put("pairing", pairing.current() != null)
            })

            val request = reader.readControl()
            val clientNonce = request.body.string("client_nonce")?.takeIf { Hex.isHex(it, Protocol.NONCE_BYTES) }
                ?: throw ProtocolException("protocol", "invalid client_nonce")
            val proof = request.body.string("proof") ?: throw ProtocolException("protocol", "missing proof")
            val fp = identity.fingerprint

            when (request.op) {
                "pair" -> {
                    val code = pairing.consume()
                    if (code == null) {
                        listener.onPairingFailed("no active pairing code")
                        throw ProtocolException("no_pairing", "pairing is not enabled on the phone")
                    }
                    val expected = Protocol.pairProof(code, fp, serverNonce, clientNonce, clientId)
                    if (!Protocol.constantTimeEquals(expected, proof)) {
                        listener.onPairingFailed("wrong code from $clientName")
                        throw ProtocolException("bad_code", "wrong pairing code; generate a new one on the phone")
                    }
                    val client = PairedClient(clientId, clientName, Protocol.randomHex(random, Protocol.TOKEN_BYTES), System.currentTimeMillis())
                    clients.put(client)
                    writer.writeControl(buildJsonObject {
                        put("op", "paired")
                        put("token", client.token)
                        put("proof", Protocol.pairAck(code, fp, serverNonce, clientNonce))
                    })
                    listener.onPaired(client)
                    return true
                }
                "auth" -> {
                    val client = clients.get(clientId)
                        ?: throw ProtocolException("unknown_client", "this computer is not paired")
                    val expected = Protocol.authProof(client.token, fp, serverNonce, clientNonce, clientId)
                    if (!Protocol.constantTimeEquals(expected, proof)) throw ProtocolException("bad_proof", "authentication failed")
                    clientName = client.name
                    return true
                }
                else -> throw ProtocolException("protocol", "expected pair or auth")
            }
        }

        private fun controlLoop(reader: MessageReader, writer: MessageWriter) {
            while (true) {
                val msg = reader.read() ?: return
                if (msg !is Message.Control) throw ProtocolException("protocol", "unexpected frame from client")
                when (msg.op) {
                    "start" -> {
                        config = parseConfig(msg.body, StreamConfig())
                        if (streaming) source.update(config) else source.start(config) { jpeg, rotation, flags, ts ->
                            slot.offer(PendingFrame(jpeg, rotation, flags, ts))
                        }
                        streaming = true
                        writer.writeControl(configMessage("started"))
                        listener.onStreaming(clientName, config)
                    }
                    "update" -> {
                        config = parseConfig(msg.body, config)
                        if (streaming) source.update(config)
                        writer.writeControl(configMessage("updated"))
                        if (streaming) listener.onStreaming(clientName, config)
                    }
                    "stop" -> {
                        if (streaming) source.stop()
                        streaming = false
                        writer.writeControl(buildJsonObject { put("op", "stopped") })
                        listener.onStreamStopped(clientName)
                    }
                    "ping" -> writer.writeControl(buildJsonObject { put("op", "pong") })
                    else -> writer.writeControl(error("protocol", "unknown op"))
                }
            }
        }

        private fun sendLoop(writer: MessageWriter) {
            try {
                while (true) {
                    val f = slot.take() ?: return
                    writer.writeFrame(f.timestampUs, f.rotation, f.flags, f.jpeg)
                }
            } catch (e: InterruptedException) {
                // session ended
            } catch (e: IOException) {
                close()
            }
        }

        private fun configMessage(op: String) = buildJsonObject {
            put("op", op)
            put("width", config.width)
            put("height", config.height)
            put("fps", config.fps)
            put("quality", config.quality)
            put("camera", config.camera)
            put("torch", config.torch)
        }

        override fun close() {
            slot.close()
            try {
                socket.close()
            } catch (_: IOException) {
            }
        }

        private inline fun tryWrite(block: () -> Unit) {
            try {
                block()
            } catch (_: IOException) {
            }
        }
    }

    private class PendingFrame(val jpeg: ByteArray, val rotation: Int, val flags: Int, val timestampUs: Long)

    /** Single-slot mailbox: a slow network drops stale frames instead of queueing latency. */
    private class FrameSlot {
        private val lock = ReentrantLock()
        private val available = lock.newCondition()
        private var frame: PendingFrame? = null
        private var closed = false

        fun offer(f: PendingFrame) = lock.withLock {
            if (!closed) {
                frame = f
                available.signal()
            }
        }

        fun take(): PendingFrame? = lock.withLock {
            while (frame == null && !closed) available.await()
            if (closed) null else frame.also { frame = null }
        }

        fun close() = lock.withLock {
            closed = true
            available.signalAll()
        }
    }

    companion object {
        const val HANDSHAKE_TIMEOUT_MS = 15_000
        const val MAX_PENDING_CONNECTIONS = 4

        fun parseConfig(body: JsonObject, base: StreamConfig) = StreamConfig(
            width = body.int("width")?.coerceIn(160, 3840) ?: base.width,
            height = body.int("height")?.coerceIn(120, 2160) ?: base.height,
            fps = body.int("fps")?.coerceIn(1, 60) ?: base.fps,
            quality = body.int("quality")?.coerceIn(30, 100) ?: base.quality,
            camera = body.string("camera")?.takeIf { it == StreamConfig.CAMERA_BACK || it == StreamConfig.CAMERA_FRONT } ?: base.camera,
            torch = body.bool("torch") ?: base.torch,
        )

        private fun sanitizeName(name: String?): String =
            name?.filter { !it.isISOControl() }?.trim()?.take(64)?.ifEmpty { null } ?: "Computer"

        private fun error(code: String, message: String) = buildJsonObject {
            put("op", "error")
            put("code", code)
            put("message", message)
        }
    }
}
