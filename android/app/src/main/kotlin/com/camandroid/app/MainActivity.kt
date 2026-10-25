package com.camandroid.app

import android.Manifest
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
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
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

    Scaffold(topBar = { TopAppBar(title = { Text("CamAndroid") }) }) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            ServerCard(state, onStart = startServer, onStop = { CameraService.stop(context) })
            if (state.running) {
                PairingCard(code)
            }
            PairedComputersCard(clients)
            state.message?.let {
                Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
            }
            HelpCard()
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
                s != null -> "Streaming to ${state.client} · ${s.width}×${s.height} @ ${s.fps} fps · ${s.camera} camera"
                state.client != null -> "Connected to ${state.client}"
                else -> "Waiting for a computer…"
            }
            Text(status, color = MaterialTheme.colorScheme.primary)
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
            Text("USB: enable Developer options › USB debugging, plug in the cable and tick \"USB\" in the desktop app.")
            Text("Video is encrypted with TLS and only paired computers can connect.")
        }
    }
}
