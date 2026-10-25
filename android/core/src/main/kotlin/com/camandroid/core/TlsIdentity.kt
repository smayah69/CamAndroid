package com.camandroid.core

import java.io.ByteArrayOutputStream
import java.math.BigInteger
import java.net.Socket
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.Principal
import java.security.PrivateKey
import java.security.SecureRandom
import java.security.Signature
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.security.spec.ECGenParameterSpec
import java.security.spec.PKCS8EncodedKeySpec
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLEngine
import javax.net.ssl.X509ExtendedKeyManager

/**
 * The phone's long-lived TLS identity: an EC P-256 key and a self-signed certificate.
 * Desktop clients pin the SHA-256 fingerprint of the certificate when pairing.
 */
class TlsIdentity(val privateKey: PrivateKey, val certificate: X509Certificate) {
    val fingerprint: String = Protocol.sha256Hex(certificate.encoded)

    /** First 16 hex chars of the fingerprint, used as the device id in discovery. */
    val deviceId: String get() = fingerprint.take(16)

    fun sslContext(): SSLContext = SSLContext.getInstance("TLS").apply {
        init(arrayOf(SingleKeyManager(privateKey, arrayOf(certificate))), null, SecureRandom())
    }

    fun encodedKey(): ByteArray = privateKey.encoded
    fun encodedCertificate(): ByteArray = certificate.encoded

    companion object {
        fun generate(commonName: String = "CamAndroid", random: SecureRandom = SecureRandom()): TlsIdentity {
            val kpg = KeyPairGenerator.getInstance("EC")
            kpg.initialize(ECGenParameterSpec("secp256r1"), random)
            val keyPair = kpg.generateKeyPair()
            val now = System.currentTimeMillis()
            val der = SelfSignedCertificate.build(
                commonName = commonName,
                publicKeyInfo = keyPair.public.encoded,
                privateKey = keyPair.private,
                serial = BigInteger(63, random).add(BigInteger.ONE),
                notBefore = Date(now - 24L * 3600 * 1000),
                notAfter = Date(now + 20L * 365 * 24 * 3600 * 1000),
            )
            return TlsIdentity(keyPair.private, parseCertificate(der))
        }

        fun decode(pkcs8Key: ByteArray, certificateDer: ByteArray): TlsIdentity {
            val key = KeyFactory.getInstance("EC").generatePrivate(PKCS8EncodedKeySpec(pkcs8Key))
            return TlsIdentity(key, parseCertificate(certificateDer))
        }

        private fun parseCertificate(der: ByteArray): X509Certificate =
            CertificateFactory.getInstance("X.509").generateCertificate(der.inputStream()) as X509Certificate
    }
}

/** Minimal DER encoder for a self-signed ecdsa-with-SHA256 X.509 v3 certificate. */
internal object SelfSignedCertificate {
    private const val OID_ECDSA_SHA256 = "1.2.840.10045.4.3.2"
    private const val OID_COMMON_NAME = "2.5.4.3"

    fun build(
        commonName: String,
        publicKeyInfo: ByteArray,
        privateKey: PrivateKey,
        serial: BigInteger,
        notBefore: Date,
        notAfter: Date,
    ): ByteArray {
        val algorithm = seq(oid(OID_ECDSA_SHA256))
        val name = seq(set(seq(oid(OID_COMMON_NAME), tlv(0x0C, commonName.toByteArray()))))
        val tbs = seq(
            tlv(0xA0, integer(BigInteger.valueOf(2))), // v3
            integer(serial),
            algorithm,
            name,
            seq(time(notBefore), time(notAfter)),
            name,
            publicKeyInfo,
        )
        val signature = Signature.getInstance("SHA256withECDSA").run {
            initSign(privateKey)
            update(tbs)
            sign()
        }
        return seq(tbs, algorithm, tlv(0x03, byteArrayOf(0) + signature))
    }

    private fun tlv(tag: Int, content: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        out.write(tag)
        val len = content.size
        when {
            len < 0x80 -> out.write(len)
            len < 0x100 -> { out.write(0x81); out.write(len) }
            len < 0x10000 -> { out.write(0x82); out.write(len shr 8); out.write(len and 0xFF) }
            else -> error("DER element too large")
        }
        out.write(content)
        return out.toByteArray()
    }

    private fun seq(vararg parts: ByteArray) = tlv(0x30, parts.fold(ByteArray(0)) { acc, p -> acc + p })
    private fun set(vararg parts: ByteArray) = tlv(0x31, parts.fold(ByteArray(0)) { acc, p -> acc + p })
    private fun integer(v: BigInteger) = tlv(0x02, v.toByteArray())

    private fun oid(dotted: String): ByteArray {
        val parts = dotted.split('.').map { it.toLong() }
        val out = ByteArrayOutputStream()
        out.write((parts[0] * 40 + parts[1]).toInt())
        for (p in parts.drop(2)) {
            val bytes = mutableListOf<Int>()
            var v = p
            bytes.add((v and 0x7F).toInt())
            v = v shr 7
            while (v > 0) {
                bytes.add(((v and 0x7F) or 0x80).toInt())
                v = v shr 7
            }
            bytes.reversed().forEach(out::write)
        }
        return tlv(0x06, out.toByteArray())
    }

    private fun time(date: Date): ByteArray {
        val utc = TimeZone.getTimeZone("UTC")
        val year = java.util.Calendar.getInstance(utc).apply { time = date }.get(java.util.Calendar.YEAR)
        // RFC 5280: UTCTime through 2049, GeneralizedTime from 2050.
        val (tag, pattern) = if (year < 2050) 0x17 to "yyMMddHHmmss'Z'" else 0x18 to "yyyyMMddHHmmss'Z'"
        val fmt = SimpleDateFormat(pattern, Locale.US).apply { timeZone = utc }
        return tlv(tag, fmt.format(date).toByteArray())
    }
}

/** Key manager serving exactly one EC key, avoiding any dependency on platform KeyStore types. */
private class SingleKeyManager(
    private val key: PrivateKey,
    private val chain: Array<X509Certificate>,
) : X509ExtendedKeyManager() {
    private fun matches(keyType: String?) = keyType == null || keyType in EC_KEY_TYPES

    override fun chooseServerAlias(keyType: String?, issuers: Array<out Principal>?, socket: Socket?) =
        if (matches(keyType)) ALIAS else null

    override fun chooseEngineServerAlias(keyType: String?, issuers: Array<out Principal>?, engine: SSLEngine?) =
        if (matches(keyType)) ALIAS else null

    override fun getServerAliases(keyType: String?, issuers: Array<out Principal>?) =
        if (matches(keyType)) arrayOf(ALIAS) else null

    override fun getCertificateChain(alias: String?) = if (alias == ALIAS) chain else null
    override fun getPrivateKey(alias: String?) = if (alias == ALIAS) key else null
    override fun getClientAliases(keyType: String?, issuers: Array<out Principal>?): Array<String>? = null
    override fun chooseClientAlias(keyType: Array<out String>?, issuers: Array<out Principal>?, socket: Socket?): String? = null

    private companion object {
        const val ALIAS = "camandroid"
        val EC_KEY_TYPES = setOf("EC", "EC_EC", "ECDSA", "ECDHE_ECDSA")
    }
}
