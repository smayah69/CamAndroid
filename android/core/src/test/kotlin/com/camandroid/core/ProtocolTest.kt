package com.camandroid.core

import java.security.SecureRandom
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ProtocolTest {
    // Shared with desktop/tests/test_protocol.py: both implementations must produce these values.
    private val fp = "ab".repeat(32)
    private val serverNonce = "01".repeat(16)
    private val clientNonce = "02".repeat(16)
    private val clientId = "client-0001"
    private val code = "ABCD1234EFGH"
    private val token = "cd".repeat(32)

    @Test
    fun proofsMatchSharedVectors() {
        assertEquals(
            "b420117ce53300f6481d2f6a876653da0f1df0141dc19bfaff870793af8f7a30",
            Protocol.pairProof(code, fp, serverNonce, clientNonce, clientId),
        )
        assertEquals(
            "6fbb4e30fd8c577c82860b98ed816790fa07d321aab44965edae04f5c94d936c",
            Protocol.pairAck(code, fp, serverNonce, clientNonce),
        )
        assertEquals(
            "4404b3e2063f190dafb5e1a8dfc726432bcd687351056d1444f362ae243e821c",
            Protocol.authProof(token, fp, serverNonce, clientNonce, clientId),
        )
    }

    @Test
    fun normalizesCodes() {
        assertEquals("ABCD1234EFGH", Protocol.normalizeCode("abcd-1234-efgh"))
        assertEquals("0110ABCDEFGH", Protocol.normalizeCode("OIL0 abcd efgh"))
        assertNull(Protocol.normalizeCode("ABCD-1234-EFG"))
        assertNull(Protocol.normalizeCode("ABCD-1234-EFGU"))
    }

    @Test
    fun generatedCodesAreValid() {
        val random = SecureRandom()
        repeat(100) {
            val c = Protocol.generatePairingCode(random)
            assertEquals(c, Protocol.normalizeCode(Protocol.formatCode(c)))
        }
    }

    @Test
    fun hexRoundTrip() {
        val bytes = ByteArray(256) { it.toByte() }
        assertTrue(Hex.decode(Hex.encode(bytes)).contentEquals(bytes))
        assertTrue(Hex.isHex("0a".repeat(16), 16))
    }

    @Test
    fun pairingCodeIsSingleUse() {
        var now = 0L
        val pairing = PairingManager(ttlMs = 1000, clock = { now })
        val code = pairing.newCode().code
        assertEquals(code, pairing.consume())
        assertNull(pairing.consume())
        pairing.newCode()
        now = 1000
        assertNull(pairing.consume(), "expired codes must be rejected")
    }
}
