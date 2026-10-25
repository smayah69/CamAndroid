package com.camandroid.app

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import android.util.Log
import com.camandroid.core.ClientStore
import com.camandroid.core.PairedClient
import com.camandroid.core.string
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.put
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** AES-256-GCM with a non-exportable key held in the Android Keystore. */
object SecretBox {
    private const val ALIAS = "camandroid-secrets"
    private const val IV_BYTES = 12

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return gen.generateKey()
    }

    fun encrypt(plain: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        return cipher.iv + cipher.doFinal(plain)
    }

    fun decrypt(blob: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, blob, 0, IV_BYTES))
        return cipher.doFinal(blob, IV_BYTES, blob.size - IV_BYTES)
    }
}

/** Paired computers and their tokens, stored encrypted with [SecretBox]. */
class EncryptedClientStore(file: File, private val onChange: () -> Unit) : ClientStore {
    private val atomicFile = AtomicFile(file)
    private val clients = LinkedHashMap<String, PairedClient>()

    init {
        load()
    }

    @Synchronized override fun get(id: String) = clients[id]

    override fun put(client: PairedClient) {
        synchronized(this) {
            clients[client.id] = client
            save()
        }
        onChange()
    }

    override fun remove(id: String) {
        synchronized(this) {
            clients.remove(id)
            save()
        }
        onChange()
    }

    @Synchronized override fun all() = clients.values.toList()

    private fun load() {
        if (!atomicFile.baseFile.exists()) return
        try {
            val json = Json.parseToJsonElement(SecretBox.decrypt(atomicFile.readFully()).decodeToString())
            for (item in json.jsonArray) {
                val o = item.jsonObject
                val c = PairedClient(
                    id = o.string("id") ?: continue,
                    name = o.string("name") ?: "Computer",
                    token = o.string("token") ?: continue,
                    pairedAt = o["paired_at"]?.jsonPrimitive?.long ?: 0L,
                )
                clients[c.id] = c
            }
        } catch (e: Exception) {
            Log.w("CamAndroid", "Paired computers could not be read; they will need to pair again", e)
        }
    }

    private fun save() {
        val json: JsonArray = buildJsonArray {
            for (c in clients.values) {
                add(buildJsonObject {
                    put("id", c.id)
                    put("name", c.name)
                    put("token", c.token)
                    put("paired_at", c.pairedAt)
                })
            }
        }
        val out = atomicFile.startWrite()
        try {
            out.write(SecretBox.encrypt(json.toString().toByteArray()))
            atomicFile.finishWrite(out)
        } catch (e: Exception) {
            atomicFile.failWrite(out)
            throw e
        }
    }
}
