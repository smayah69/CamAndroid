package com.camandroid.app

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import com.camandroid.core.CamServer
import com.camandroid.core.PairedClient
import com.camandroid.core.Protocol
import com.camandroid.core.StreamConfig
import kotlinx.coroutines.flow.update
import java.io.IOException
import java.net.Inet4Address
import java.net.NetworkInterface

/**
 * Foreground service that owns the TLS server, the camera and the mDNS advertisement, so streaming
 * keeps working with the screen off or the app in the background.
 */
class CameraService : LifecycleService() {
    private var server: CamServer? = null
    private var source: CameraXFrameSource? = null
    private var registration: NsdManager.RegistrationListener? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (server == null) startServer()
        return START_NOT_STICKY
    }

    private fun startServer() {
        createChannel()
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA else 0
        try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID, notification(getString(R.string.notification_waiting)), type)
        } catch (e: Exception) {
            // Android refuses camera foreground services started from the background.
            Log.e(TAG, "Cannot start foreground service", e)
            AppGraph.report("Could not start: ${e.message}")
            stopSelf()
            return
        }

        val frameSource = CameraXFrameSource(this, this) { AppGraph.report(it) }
        val srv = CamServer(
            AppGraph.identity, AppGraph.clientStore, AppGraph.pairing, frameSource, AppGraph.deviceName(), listener,
        )
        val port = try {
            srv.start(Protocol.DEFAULT_PORT)
        } catch (e: IOException) {
            AppGraph.report("Port ${Protocol.DEFAULT_PORT} is in use: ${e.message}")
            frameSource.release()
            stopSelf()
            return
        }
        server = srv
        source = frameSource
        AppGraph.server = srv
        AppGraph.state.value = ServerState(running = true, port = port, addresses = localAddresses())
        advertise(port)
    }

    private val listener = object : CamServer.Listener {
        override fun onPaired(client: PairedClient) {
            AppGraph.report("Paired with ${client.name}")
        }

        override fun onPairingFailed(reason: String) {
            AppGraph.report("Pairing failed: $reason. Show a new code to try again.")
        }

        override fun onSessionStarted(clientName: String) {
            AppGraph.state.update { it.copy(client = clientName, streaming = null, message = null) }
            updateNotification(getString(R.string.notification_connected, clientName))
        }

        override fun onStreaming(clientName: String, config: StreamConfig) {
            AppGraph.state.update { it.copy(client = clientName, streaming = config) }
            updateNotification(getString(R.string.notification_streaming, clientName))
            acquireLocks()
        }

        override fun onStreamStopped(clientName: String) {
            AppGraph.state.update { it.copy(streaming = null) }
            updateNotification(getString(R.string.notification_connected, clientName))
            releaseLocks()
        }

        override fun onSessionEnded(clientName: String) {
            AppGraph.state.update { it.copy(client = null, streaming = null) }
            updateNotification(getString(R.string.notification_waiting))
            releaseLocks()
        }
    }

    override fun onDestroy() {
        registration?.let { reg ->
            try {
                getSystemService(NsdManager::class.java).unregisterService(reg)
            } catch (e: IllegalArgumentException) {
                // never registered
            }
        }
        registration = null
        server?.close()
        server = null
        AppGraph.server = null
        source?.release()
        source = null
        releaseLocks()
        AppGraph.state.update { ServerState(message = it.message) }
        super.onDestroy()
    }

    private fun advertise(port: Int) {
        val info = NsdServiceInfo().apply {
            serviceName = "CamAndroid ${AppGraph.deviceName()}"
            serviceType = Protocol.NSD_SERVICE_TYPE
            setPort(port)
            setAttribute("id", AppGraph.identity.deviceId)
            setAttribute("v", Protocol.VERSION.toString())
        }
        val reg = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(info: NsdServiceInfo) {}
            override fun onServiceUnregistered(info: NsdServiceInfo) {}
            override fun onRegistrationFailed(info: NsdServiceInfo, errorCode: Int) {
                Log.w(TAG, "mDNS registration failed: $errorCode")
            }
            override fun onUnregistrationFailed(info: NsdServiceInfo, errorCode: Int) {}
        }
        try {
            getSystemService(NsdManager::class.java).registerService(info, NsdManager.PROTOCOL_DNS_SD, reg)
            registration = reg
        } catch (e: Exception) {
            Log.w(TAG, "mDNS unavailable", e)
        }
    }

    @SuppressLint("WakelockTimeout") // held exactly while a computer is streaming
    private fun acquireLocks() {
        if (wakeLock == null) {
            wakeLock = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "CamAndroid:stream")
                .apply { acquire() }
        }
        if (wifiLock == null) {
            val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                WifiManager.WIFI_MODE_FULL_LOW_LATENCY
            } else {
                @Suppress("DEPRECATION")
                WifiManager.WIFI_MODE_FULL_HIGH_PERF
            }
            wifiLock = applicationContext.getSystemService(WifiManager::class.java)
                .createWifiLock(mode, "CamAndroid:stream")
                .apply { acquire() }
        }
    }

    private fun releaseLocks() {
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        wifiLock?.takeIf { it.isHeld }?.release()
        wifiLock = null
    }

    private fun createChannel() {
        val channel = NotificationChannel(CHANNEL_ID, getString(R.string.channel_name), NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, CameraService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(0, getString(R.string.action_stop), stop)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    @SuppressLint("MissingPermission") // checked via areNotificationsEnabled()
    private fun updateNotification(text: String) {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU || nm.areNotificationsEnabled()) {
            nm.notify(NOTIFICATION_ID, notification(text))
        }
    }

    companion object {
        private const val TAG = "CamAndroid"
        private const val CHANNEL_ID = "server"
        private const val NOTIFICATION_ID = 1
        private const val ACTION_STOP = "com.camandroid.app.STOP"

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, CameraService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, CameraService::class.java))
        }

        fun localAddresses(): List<String> = try {
            NetworkInterface.getNetworkInterfaces().toList()
                .filter { it.isUp && !it.isLoopback }
                .flatMap { it.inetAddresses.toList() }
                .filterIsInstance<Inet4Address>()
                .mapNotNull { it.hostAddress }
        } catch (e: Exception) {
            emptyList()
        }
    }
}
