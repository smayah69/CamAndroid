package com.camandroid.app

import android.app.Application
import android.content.Context
import android.provider.Settings
import android.util.Log
import com.camandroid.core.CamServer
import com.camandroid.core.PairingManager
import com.camandroid.core.PairedClient
import com.camandroid.core.Protocol
import com.camandroid.core.StreamConfig
import com.camandroid.core.TlsIdentity
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import java.io.File

class CamAndroidApp : Application() {
    override fun onCreate() {
        super.onCreate()
        AppGraph.init(this)
    }
}

data class ServerState(
    val running: Boolean = false,
    val port: Int = Protocol.DEFAULT_PORT,
    val addresses: List<String> = emptyList(),
    val client: String? = null,
    val streaming: StreamConfig? = null,
    val message: String? = null,
    /** When the server stops by itself if no computer connects (wall-clock ms), or null. */
    val idleStopAt: Long? = null,
)

/** Process-wide singletons shared by the UI and [CameraService]. */
object AppGraph {
    private const val TAG = "CamAndroid"
    private lateinit var app: Context

    val state = MutableStateFlow(ServerState())
    val pairingCode = MutableStateFlow<PairingManager.ActiveCode?>(null)
    val pairedClients = MutableStateFlow<List<PairedClient>>(emptyList())

    /** Set when the desktop app opens us over USB (adb) to start the server right away. */
    val autoStart = MutableStateFlow(false)

    val pairing = PairingManager().apply { onChange = { pairingCode.value = it } }

    val clientStore by lazy {
        EncryptedClientStore(File(app.filesDir, "clients.bin")) { refreshClients() }
    }

    val identity: TlsIdentity by lazy { loadOrCreateIdentity() }

    /** Set by [CameraService] while the server runs. */
    @Volatile var server: CamServer? = null

    fun init(context: Context) {
        app = context.applicationContext
        refreshClients()
    }

    fun refreshClients() {
        pairedClients.value = clientStore.all().sortedByDescending { it.pairedAt }
    }

    fun forgetClient(id: String) {
        val name = pairedClients.value.firstOrNull { it.id == id }?.name
        clientStore.remove(id)
        if (name != null && state.value.client == name) server?.disconnectClient()
    }

    fun report(message: String) {
        state.update { it.copy(message = message) }
    }

    fun deviceName(): String =
        Settings.Global.getString(app.contentResolver, Settings.Global.DEVICE_NAME)?.takeIf { it.isNotBlank() }
            ?: android.os.Build.MODEL

    private fun loadOrCreateIdentity(): TlsIdentity {
        val keyFile = File(app.filesDir, "identity.key")
        val certFile = File(app.filesDir, "identity.crt")
        if (keyFile.exists() && certFile.exists()) {
            try {
                return TlsIdentity.decode(SecretBox.decrypt(keyFile.readBytes()), certFile.readBytes())
            } catch (e: Exception) {
                // Keystore wiped (e.g. lock screen removed) or file corrupt: clients must re-pair.
                Log.w(TAG, "Could not load identity, generating a new one", e)
            }
        }
        val identity = TlsIdentity.generate("CamAndroid ${deviceName()}")
        keyFile.writeBytes(SecretBox.encrypt(identity.encodedKey()))
        certFile.writeBytes(identity.encodedCertificate())
        return identity
    }
}
