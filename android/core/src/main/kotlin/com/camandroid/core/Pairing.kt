package com.camandroid.core

import java.security.SecureRandom

data class PairedClient(val id: String, val name: String, val token: String, val pairedAt: Long)

interface ClientStore {
    fun get(id: String): PairedClient?
    fun put(client: PairedClient)
    fun remove(id: String)
    fun all(): List<PairedClient>
}

class InMemoryClientStore : ClientStore {
    private val clients = LinkedHashMap<String, PairedClient>()

    @Synchronized override fun get(id: String) = clients[id]
    @Synchronized override fun put(client: PairedClient) { clients[client.id] = client }
    @Synchronized override fun remove(id: String) { clients.remove(id) }
    @Synchronized override fun all() = clients.values.toList()
}

/**
 * Holds the one-time pairing code currently shown on the phone. A code is valid for [ttlMs] and
 * for a single pairing attempt: [consume] invalidates it whether or not the attempt succeeds, so
 * an attacker gets exactly one guess per code the user chooses to display.
 */
class PairingManager(
    private val ttlMs: Long = 5 * 60 * 1000L,
    private val clock: () -> Long = System::currentTimeMillis,
    private val random: SecureRandom = SecureRandom(),
) {
    data class ActiveCode(val code: String, val expiresAt: Long)

    @Volatile var onChange: ((ActiveCode?) -> Unit)? = null
    private var active: ActiveCode? = null

    fun newCode(): ActiveCode {
        val code = ActiveCode(Protocol.generatePairingCode(random), clock() + ttlMs)
        synchronized(this) { active = code }
        onChange?.invoke(code)
        return code
    }

    @Synchronized
    fun current(): ActiveCode? = active?.takeIf { clock() < it.expiresAt }

    fun cancel() {
        synchronized(this) { active = null }
        onChange?.invoke(null)
    }

    /** Takes the active code for one verification attempt, or returns null if none is valid. */
    fun consume(): String? {
        val code = synchronized(this) {
            val c = active?.takeIf { clock() < it.expiresAt }
            active = null
            c
        }
        onChange?.invoke(null)
        return code?.code
    }
}
