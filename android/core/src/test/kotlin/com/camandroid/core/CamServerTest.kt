package com.camandroid.core

import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import java.security.Signature
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class CamServerTest {
    private val identity = TlsIdentity.generate()
    private val store = InMemoryClientStore()
    private val pairing = PairingManager()
    private val source = FakeFrameSource()
    private lateinit var server: CamServer
    private var port = 0

    @BeforeTest
    fun setUp() {
        server = CamServer(identity, store, pairing, source, "Test Phone")
        port = server.start(0)
    }

    @AfterTest
    fun tearDown() = server.close()

    @Test
    fun certificateIsValidSelfSigned() {
        val cert = identity.certificate
        cert.checkValidity()
        cert.verify(cert.publicKey)
        assertEquals(3, cert.version)
        val decoded = TlsIdentity.decode(identity.encodedKey(), identity.encodedCertificate())
        assertEquals(identity.fingerprint, decoded.fingerprint)
        val sig = Signature.getInstance("SHA256withECDSA").apply { initSign(decoded.privateKey); update(byteArrayOf(1)) }.sign()
        assertTrue(Signature.getInstance("SHA256withECDSA").apply { initVerify(cert.publicKey); update(byteArrayOf(1)) }.verify(sig))
    }

    @Test
    fun pairThenAuthenticateAndStream() {
        val code = pairing.newCode().code
        val token = TestClient(port).use { client ->
            assertEquals(identity.fingerprint, client.fingerprint)
            val reply = client.pair(Protocol.formatCode(code).let(Protocol::normalizeCode)!!)
            assertEquals("paired", reply.string("op"))
            assertEquals("auth_ok", client.reader.readControl().op)
            reply.string("token")!!
        }
        assertEquals("Test PC", store.get("test-client-1")?.name)

        // The server frees the session slot asynchronously after the first client disconnects.
        Thread.sleep(200)
        TestClient(port).use { client ->
            assertEquals("auth_ok", client.auth(token).string("op"))
            client.writer.writeControl(buildJsonObject {
                put("op", "start"); put("width", 320); put("height", 240); put("fps", 30); put("camera", "front")
            })
            assertEquals("started", client.reader.readControl().op)
            val frame = client.reader.read() as Message.Frame
            assertEquals(Protocol.FLAG_FRONT_CAMERA or Protocol.FLAG_KEY_FRAME, frame.flags)
            assertTrue(frame.data[0] == 0xFF.toByte() && frame.data[1] == 0xD8.toByte(), "frame must be a JPEG")
            assertEquals(320, source.config?.width)
        }
    }

    @Test
    fun codecIsNegotiated() {
        store.put(PairedClient("test-client-1", "a", "11".repeat(32), 0))
        source.supportedCodecs = listOf(StreamConfig.CODEC_H264, StreamConfig.CODEC_JPEG)
        TestClient(port).use { client ->
            val ok = client.auth("11".repeat(32))
            assertEquals(listOf("h264", "jpeg"), ok["codecs"]?.jsonArray?.map { it.jsonPrimitive.content })
            client.writer.writeControl(buildJsonObject {
                put("op", "start"); put("width", 320); put("height", 240)
                putJsonArray("codecs") { add("vp9"); add("h264") }
            })
            val started = client.reader.readControl()
            assertEquals("h264", started.body.string("codec"))
            assertEquals(StreamConfig.CODEC_H264, source.config?.codec)
        }
    }

    @Test
    fun oldClientsGetJpeg() {
        source.supportedCodecs = listOf(StreamConfig.CODEC_H264, StreamConfig.CODEC_JPEG)
        val body = buildJsonObject { put("op", "start") }
        assertEquals(StreamConfig.CODEC_JPEG, CamServer.negotiateCodec(body, source.supportedCodecs))
    }

    @Test
    fun queueKeepsLatencyLow() {
        var keyRequests = 0
        val q = CamServer.FrameQueue { keyRequests++ }
        q.maxBacklog = 3
        fun frame(n: Long, key: Boolean) =
            CamServer.PendingFrame(byteArrayOf(n.toByte()), 0, if (key) Protocol.FLAG_KEY_FRAME else 0, n)

        // A key frame replaces everything queued before it.
        q.offer(frame(1, true)); q.offer(frame(2, false)); q.offer(frame(3, true))
        assertEquals(3L, q.take()?.timestampUs)

        // Overflow drops the backlog, requests a key frame and skips delta frames until it arrives.
        q.offer(frame(4, false)); q.offer(frame(5, false)); q.offer(frame(6, false))
        q.offer(frame(7, false))
        assertEquals(1, keyRequests)
        q.offer(frame(8, false))
        q.offer(frame(9, true)); q.offer(frame(10, false))
        assertEquals(9L, q.take()?.timestampUs)
        assertEquals(10L, q.take()?.timestampUs)
    }

    @Test
    fun bitrateScalesWithPixelsAndQuality() {
        val hd60 = StreamConfig(1280, 720, 60, 80).bitrate
        assertTrue(hd60 in 5_000_000..8_000_000, "720p60 bitrate $hd60")
        assertTrue(StreamConfig(1920, 1080, 60, 80).bitrate > hd60)
        assertTrue(StreamConfig(1280, 720, 60, 40).bitrate < hd60)
    }

    @Test
    fun wrongCodeIsRejectedAndBurnsTheCode() {
        pairing.newCode()
        TestClient(port).use { client ->
            val reply = client.pair("ZZZZZZZZZZZZ")
            assertEquals("error", reply.string("op"))
            assertEquals("bad_code", reply.string("code"))
        }
        assertEquals(null, pairing.current())
        assertTrue(store.all().isEmpty())
    }

    @Test
    fun pairingWithoutActiveCodeIsRejected() {
        TestClient(port).use { client ->
            assertEquals("no_pairing", client.pair("ABCD1234EFGH").string("code"))
        }
    }

    @Test
    fun unknownTokenIsRejected() {
        TestClient(port).use { client ->
            assertEquals("unknown_client", client.auth("00".repeat(32)).string("code"))
        }
        store.put(PairedClient("test-client-1", "x", "11".repeat(32), 0))
        TestClient(port).use { client ->
            assertEquals("bad_proof", client.auth("00".repeat(32)).string("code"))
        }
    }

    @Test
    fun onlyOneClientAtATime() {
        store.put(PairedClient("test-client-1", "a", "11".repeat(32), 0))
        store.put(PairedClient("test-client-2", "b", "22".repeat(32), 0))
        TestClient(port, "test-client-1").use { first ->
            assertEquals("auth_ok", first.auth("11".repeat(32)).string("op"))
            TestClient(port, "test-client-2").use { second ->
                assertEquals("busy", second.auth("22".repeat(32)).string("code"))
            }
        }
    }
}
