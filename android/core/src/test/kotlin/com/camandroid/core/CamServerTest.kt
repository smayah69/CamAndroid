package com.camandroid.core

import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
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
            assertEquals(Protocol.FLAG_FRONT_CAMERA, frame.flags)
            assertTrue(frame.jpeg[0] == 0xFF.toByte() && frame.jpeg[1] == 0xD8.toByte(), "frame must be a JPEG")
            assertEquals(320, source.config?.width)
        }
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
