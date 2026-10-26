package com.camandroid.app

import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import com.camandroid.core.Hex
import com.camandroid.core.int
import com.camandroid.core.string
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import kotlin.concurrent.thread

/**
 * Checks https://bombo.cu.ma/extras/camandroid/version.json (written by CI on every release),
 * downloads the new APK from the same server and hands it to Android's package installer.
 *
 * Android only installs an update signed with the same key as the installed app; the download is
 * also checked against the SHA-256 in version.json and its signer is compared up front, so a bad
 * file fails with a clear message instead of "App not installed".
 */
object Updater {
    const val VERSION_URL = "https://bombo.cu.ma/extras/camandroid/version.json"
    private const val HOST = "bombo.cu.ma"
    private const val TAG = "CamAndroid"

    data class Update(val versionName: String, val versionCode: Long, val url: String, val sha256: String, val size: Long)

    sealed interface State {
        data object Idle : State
        data object Checking : State
        data object UpToDate : State
        data class Available(val update: Update) : State
        data class Downloading(val update: Update, val progress: Float) : State
        data class Ready(val update: Update, val file: File) : State
        data class Failed(val message: String) : State
    }

    val state = MutableStateFlow<State>(State.Idle)

    /** Only the release build updates itself; debug builds are a different app (.debug). */
    val enabled: Boolean get() = BuildConfig.APPLICATION_ID == "com.camandroid.app"

    /** Checks in the background (every time the app opens, and on demand); [quiet] hides errors. */
    fun check(quiet: Boolean) {
        if (!enabled) return
        val s = state.value
        if (s is State.Checking || s is State.Downloading || s is State.Ready) return
        if (!quiet) state.value = State.Checking
        thread(name = "update-check", isDaemon = true) {
            state.value = try {
                val update = fetch()
                if (update.versionCode > BuildConfig.VERSION_CODE) State.Available(update) else State.UpToDate
            } catch (e: Exception) {
                Log.w(TAG, "Update check failed", e)
                if (quiet) State.Idle else State.Failed("Could not check for updates: ${e.message}")
            }
        }
    }

    fun download(context: Context, update: Update) {
        val app = context.applicationContext
        state.value = State.Downloading(update, 0f)
        thread(name = "update-download", isDaemon = true) {
            state.value = try {
                State.Ready(update, fetchApk(app, update))
            } catch (e: Exception) {
                Log.w(TAG, "Update download failed", e)
                State.Failed("Download failed: ${e.message}")
            }
        }
    }

    /** Opens Android's installer for a verified download (asking for "install unknown apps" first). */
    fun install(context: Context, file: File) {
        if (!context.packageManager.canRequestPackageInstalls()) {
            state.value = State.Failed("Allow CamAndroid to install apps (the switch on the next screen), then come back and tap Update again.")
            context.startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            )
            return
        }
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.updates", file)
        context.startActivity(
            Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }

    private fun open(url: String): HttpURLConnection {
        val u = URL(url)
        if (u.protocol != "https" || u.host != HOST) throw IOException("refusing to download from $url")
        return (u.openConnection() as HttpURLConnection).apply {
            connectTimeout = 10_000
            readTimeout = 30_000
            useCaches = false
            instanceFollowRedirects = false // a redirect could leave bombo.cu.ma
            setRequestProperty("Cache-Control", "no-cache")
            setRequestProperty("User-Agent", "CamAndroid/${BuildConfig.VERSION_NAME} (Android)")
        }
    }

    private fun fetch(): Update {
        val conn = open(VERSION_URL)
        try {
            if (conn.responseCode != 200) throw IOException("HTTP ${conn.responseCode}")
            val body = Json.parseToJsonElement(conn.inputStream.use { it.readBytes().decodeToString() }).jsonObject
            val code = body.int("versionCode") ?: throw IOException("version.json has no versionCode")
            val sha = body.string("sha256")?.lowercase()?.takeIf { Hex.isHex(it, 32) }
                ?: throw IOException("version.json has no valid sha256")
            return Update(
                versionName = body.string("versionName") ?: code.toString(),
                versionCode = code.toLong(),
                url = body.string("url") ?: throw IOException("version.json has no url"),
                sha256 = sha,
                size = body.int("size")?.toLong() ?: 0L,
            )
        } finally {
            conn.disconnect()
        }
    }

    private fun fetchApk(context: Context, update: Update): File {
        val dir = File(context.cacheDir, "updates").apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() } // only ever keep the download in progress
        val file = File(dir, "CamAndroid-${update.versionCode}.apk")
        val digest = MessageDigest.getInstance("SHA-256")
        val conn = open(update.url)
        try {
            if (conn.responseCode != 200) throw IOException("HTTP ${conn.responseCode}")
            val total = conn.contentLengthLong.takeIf { it > 0 } ?: update.size
            var done = 0L
            var lastReport = 0L
            conn.inputStream.use { input ->
                file.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        digest.update(buf, 0, n)
                        done += n
                        if (total > 0 && done - lastReport > 128 * 1024) {
                            lastReport = done
                            state.value = State.Downloading(update, (done.toFloat() / total).coerceIn(0f, 1f))
                        }
                    }
                }
            }
        } finally {
            conn.disconnect()
        }
        val sha = Hex.encode(digest.digest())
        if (sha != update.sha256) {
            file.delete()
            throw IOException("the file is corrupted (SHA-256 mismatch); try again")
        }
        verifyApk(context, file, update)
        return file
    }

    private fun verifyApk(context: Context, file: File, update: Update) {
        val pm = context.packageManager
        val archive = archiveInfo(pm, file) ?: throw IOException("the download is not a valid APK")
        if (archive.packageName != context.packageName) throw IOException("the download is a different app (${archive.packageName})")
        if (archive.longVersionCodeCompat() < update.versionCode) throw IOException("the download is an older version")
        val installed = installedSigners(pm, context.packageName)
        val incoming = archiveSigners(archive)
        if (installed.isNotEmpty() && incoming.isNotEmpty() && installed.intersect(incoming).isEmpty()) {
            file.delete()
            throw IOException("the update is signed with a different key; uninstall CamAndroid and install it from the website")
        }
    }

    private fun archiveInfo(pm: PackageManager, file: File): PackageInfo? {
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) PackageManager.GET_SIGNING_CERTIFICATES else signaturesFlag()
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            pm.getPackageArchiveInfo(file.path, PackageManager.PackageInfoFlags.of(flags.toLong()))
        } else {
            @Suppress("DEPRECATION")
            pm.getPackageArchiveInfo(file.path, flags)
        }
    }

    private fun installedSigners(pm: PackageManager, packageName: String): Set<String> {
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) PackageManager.GET_SIGNING_CERTIFICATES else signaturesFlag()
        val info = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            pm.getPackageInfo(packageName, PackageManager.PackageInfoFlags.of(flags.toLong()))
        } else {
            @Suppress("DEPRECATION")
            pm.getPackageInfo(packageName, flags)
        }
        return archiveSigners(info)
    }

    /** SHA-256 of each signing certificate. */
    private fun archiveSigners(info: PackageInfo): Set<String> {
        val certs = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            info.signingInfo?.let { if (it.hasMultipleSigners()) it.apkContentsSigners else it.signingCertificateHistory }
        } else {
            @Suppress("DEPRECATION")
            info.signatures
        }
        return certs.orEmpty().mapNotNull { it?.toByteArray() }
            .map { Hex.encode(MessageDigest.getInstance("SHA-256").digest(it)) }.toSet()
    }

    @Suppress("DEPRECATION")
    private fun signaturesFlag() = PackageManager.GET_SIGNATURES

    private fun PackageInfo.longVersionCodeCompat(): Long =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) longVersionCode else @Suppress("DEPRECATION") versionCode.toLong()
}
