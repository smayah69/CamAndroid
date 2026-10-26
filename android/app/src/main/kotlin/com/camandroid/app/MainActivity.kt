package com.camandroid.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.camandroid.core.PairedClient
import com.camandroid.core.PairingManager
import com.camandroid.core.Protocol
import kotlinx.coroutines.delay
import java.text.DateFormat
import java.util.Date

class MainActivity : ComponentActivity() {
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action == ACTION_START_SERVER) AppGraph.autoStart.value = true
    }

    companion object {
        /** Sent by the desktop app over USB: `adb shell am start -a <this> -n <package>/.MainActivity`. */
        const val ACTION_START_SERVER = "com.camandroid.app.action.START_SERVER"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        handleIntent(intent)
        if (savedInstanceState == null) Updater.check(quiet = true) // every launch, not on rotation
        setContent {
            CamAndroidTheme {
                MainScreen()
            }
        }
    }
}

@Composable
private fun CamAndroidTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val colors = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> darkColorScheme()
        else -> lightColorScheme()
    }
    MaterialTheme(colorScheme = colors, content = content)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MainScreen() {
    val context = LocalContext.current
    val state by AppGraph.state.collectAsStateWithLifecycle()
    val code by AppGraph.pairingCode.collectAsStateWithLifecycle()
    val clients by AppGraph.pairedClients.collectAsStateWithLifecycle()
    val update by Updater.state.collectAsStateWithLifecycle()

    val permissions = remember {
        buildList {
            add(Manifest.permission.CAMERA)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
        }.toTypedArray()
    }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
        if (result[Manifest.permission.CAMERA] == true) {
            CameraService.start(context)
        } else {
            AppGraph.report("Camera permission is required to use the phone as a webcam.")
        }
    }
    val startServer = {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        if (granted) CameraService.start(context) else launcher.launch(permissions)
    }

    val autoStart by AppGraph.autoStart.collectAsStateWithLifecycle()
    LaunchedEffect(autoStart) {
        if (autoStart) {
            AppGraph.autoStart.value = false
            startServer() // asks for the camera permission first if needed
        }
    }

    Scaffold(topBar = { TopAppBar(title = { Text("CamAndroid") }) }) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            if (update is Updater.State.Available || update is Updater.State.Downloading || update is Updater.State.Ready) {
                UpdateCard(update, streaming = state.streaming != null)
            }
            ServerCard(state, onStart = startServer, onStop = { CameraService.stop(context) })
            if (state.running) {
                PairingCard(code)
            }
            PairedComputersCard(clients)
            state.message?.let {
                Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
            }
            HelpCard()
            AboutCard(update)
        }
    }
}

@Composable
private fun ServerCard(state: ServerState, onStart: () -> Unit, onStop: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Webcam server", style = MaterialTheme.typography.titleMedium)
            if (!state.running) {
                Text("Stopped. Start it, then connect from the CamAndroid desktop app.")
                Button(onClick = onStart, modifier = Modifier.fillMaxWidth()) { Text("Start") }
                return@Column
            }
            val addresses = state.addresses.ifEmpty { listOf("no network") }
            Text("Listening on:")
            addresses.forEach { Text("$it:${state.port}", fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold) }
            val s = state.streaming
            val status = when {
                s != null -> "Streaming to ${state.client} · ${s.width}×${s.height} @ ${s.fps} fps · ${s.codec.uppercase()} · ${s.camera} camera"
                state.client != null -> "Connected to ${state.client}"
                else -> "Waiting for a computer…"
            }
            Text(status, color = MaterialTheme.colorScheme.primary)
            state.idleStopAt?.let { stopAt ->
                var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
                LaunchedEffect(stopAt) {
                    while (true) {
                        now = System.currentTimeMillis()
                        delay(1000)
                    }
                }
                val left = ((stopAt - now) / 1000).coerceAtLeast(0)
                Text(
                    "Stops by itself in ${left / 60}:${"%02d".format(left % 60)} if no computer connects.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (state.client != null) {
                    OutlinedButton(onClick = { AppGraph.server?.disconnectClient() }) { Text("Disconnect") }
                }
                Button(onClick = onStop) { Text("Stop") }
            }
        }
    }
}

@Composable
private fun PairingCard(code: PairingManager.ActiveCode?) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Pair a computer", style = MaterialTheme.typography.titleMedium)
            if (code == null) {
                Text("Show a one-time code and type it into the desktop app. Each code works once.")
                Button(onClick = { AppGraph.pairing.newCode() }, modifier = Modifier.fillMaxWidth()) {
                    Text("Pair new computer")
                }
            } else {
                var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
                LaunchedEffect(code) {
                    while (true) {
                        now = System.currentTimeMillis()
                        if (now >= code.expiresAt) {
                            AppGraph.pairing.cancel()
                            break
                        }
                        delay(1000)
                    }
                }
                val remaining = ((code.expiresAt - now) / 1000).coerceAtLeast(0)
                Text(
                    Protocol.formatCode(code.code),
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                    fontSize = 30.sp,
                )
                Text(
                    "Expires in ${remaining / 60}:${"%02d".format(remaining % 60)} · certificate " +
                        AppGraph.identity.fingerprint.take(16).chunked(4).joinToString(" ").uppercase(),
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center,
                )
                TextButton(onClick = { AppGraph.pairing.cancel() }, modifier = Modifier.align(Alignment.End)) {
                    Text("Cancel")
                }
            }
        }
    }
}

@Composable
private fun PairedComputersCard(clients: List<PairedClient>) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Paired computers", style = MaterialTheme.typography.titleMedium)
            if (clients.isEmpty()) Text("None yet.")
            val fmt = remember { DateFormat.getDateInstance(DateFormat.MEDIUM) }
            clients.forEachIndexed { i, c ->
                if (i > 0) HorizontalDivider()
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(c.name, fontWeight = FontWeight.Medium)
                        Text("Paired ${fmt.format(Date(c.pairedAt))}", style = MaterialTheme.typography.bodySmall)
                    }
                    Spacer(Modifier.width(8.dp))
                    TextButton(onClick = { AppGraph.forgetClient(c.id) }) { Text("Forget") }
                }
            }
        }
    }
}

@Composable
private fun HelpCard() {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("How to connect", style = MaterialTheme.typography.titleMedium)
            Text("Wi-Fi: phone and computer on the same network; the desktop app finds the phone automatically.")
            Text("USB: enable Developer options › USB debugging and plug in the cable; the phone shows up as USB in the desktop app.")
            Text("Video is encrypted with TLS and only paired computers can connect.")
        }
    }
}

@Composable
private fun UpdateCard(update: Updater.State, streaming: Boolean) {
    val context = LocalContext.current
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            when (update) {
                is Updater.State.Available -> {
                    Text("Update available: ${update.update.versionName}", style = MaterialTheme.typography.titleMedium)
                    val mb = update.update.size / 1_000_000.0
                    Text("You have ${BuildConfig.VERSION_NAME}." + if (mb > 0) " Download size ${"%.1f".format(mb)} MB." else "")
                    if (streaming) Text("Updating stops the webcam.", color = MaterialTheme.colorScheme.error)
                    Button(onClick = { Updater.download(context, update.update) }, modifier = Modifier.fillMaxWidth()) {
                        Text("Download update")
                    }
                }
                is Updater.State.Downloading -> {
                    Text("Downloading ${update.update.versionName}…", style = MaterialTheme.typography.titleMedium)
                    LinearProgressIndicator(progress = { update.progress }, modifier = Modifier.fillMaxWidth())
                }
                is Updater.State.Ready -> {
                    Text("CamAndroid ${update.update.versionName} is ready", style = MaterialTheme.typography.titleMedium)
                    Text("Verified download. Android will ask you to confirm the update.")
                    Button(onClick = { Updater.install(context, update.file) }, modifier = Modifier.fillMaxWidth()) {
                        Text("Update")
                    }
                }
                else -> Unit
            }
        }
    }
}

@Composable
private fun AboutCard(update: Updater.State) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("About", style = MaterialTheme.typography.titleMedium)
            Text("CamAndroid ${BuildConfig.VERSION_NAME} by Omar Kharrat (@smayah69)")
            if (!Updater.enabled) {
                Text("Debug build: updates are off.", style = MaterialTheme.typography.bodySmall)
                return@Column
            }
            val status = when (update) {
                Updater.State.Checking -> "Checking for updates…"
                Updater.State.UpToDate -> "You have the latest version."
                is Updater.State.Failed -> update.message
                is Updater.State.Available -> "Version ${update.update.versionName} is available."
                else -> null
            }
            status?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodySmall,
                    color = if (update is Updater.State.Failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            OutlinedButton(
                onClick = { Updater.check(quiet = false) },
                enabled = update !is Updater.State.Checking && update !is Updater.State.Downloading,
            ) { Text("Check for updates") }
        }
    }
}
