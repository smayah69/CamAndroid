package com.camandroid.app

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.res.colorResource
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/** Screens reachable from the menu on the left. */
enum class Page(val label: String) { Home("Webcam"), Help("Help"), About("About") }

private const val GITHUB_URL = "https://github.com/smayah69/CamAndroid"
private const val LINKEDIN_URL = "https://www.linkedin.com/in/smayah/"

/** The three-line "hamburger" icon (drawn here to avoid the large material-icons library). */
val MenuIcon: ImageVector = ImageVector.Builder("Menu", 24.dp, 24.dp, 24f, 24f).apply {
    path(fill = SolidColor(Color.Black)) {
        for (y in listOf(6f, 11f, 16f)) {
            moveTo(3f, y)
            lineTo(21f, y)
            lineTo(21f, y + 2f)
            lineTo(3f, y + 2f)
            close()
        }
    }
}.build()

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            content()
        }
    }
}

@Composable
fun HelpPage() {
    Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Section("Get started") {
            Text("1. Install CamAndroid on your Windows PC (from the GitHub releases).")
            Text("2. Here, tap Start, then Pair new computer. A 12-character code appears.")
            Text("3. On the PC, select this phone, click Pair… and type the code. This is needed only once.")
            Text("4. Click Start webcam on the PC, then choose \"CamAndroid\" as the camera in Zoom, Teams, Meet, Discord or OBS.")
        }
        Section("Wi-Fi") {
            Text("Keep the phone and the PC on the same network; the PC finds the phone by itself.")
            Text("If it doesn't (guest Wi-Fi, some routers), click Pair… on the PC with no phone selected and type the address shown on the Webcam page.")
            Text("5 GHz Wi-Fi gives the smoothest picture.")
        }
        Section("USB") {
            Text("Turn on Developer options › USB debugging, plug in the cable and accept \"Allow USB debugging\" (tick Always allow).")
            Text("The phone then shows up as USB on the PC, and starting the webcam opens this app and its server by itself.")
        }
        Section("Good to know") {
            Text("The server keeps running with the screen off, and stops by itself after 2 minutes without a computer.")
            Text("Video is encrypted (TLS) and only computers you paired can connect. Forget a computer to remove its access.")
            Text("The front camera is mirrored like a mirror; the Mirror box on the PC flips either camera.")
        }
    }
}

@Composable
fun AboutPage(update: Updater.State) {
    val context = LocalContext.current
    val uri = LocalUriHandler.current
    Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Card(modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(64.dp)
                        .clip(RoundedCornerShape(16.dp))
                        .background(colorResource(R.color.ic_launcher_background)),
                ) {
                    Image(painterResource(R.drawable.ic_launcher_foreground), contentDescription = null)
                }
                Spacer(Modifier.width(16.dp))
                Column {
                    Text("CamAndroid", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text("Version ${BuildConfig.VERSION_NAME}", style = MaterialTheme.typography.bodyMedium)
                }
            }
            Text(
                "Use your Android phone as a webcam for your Windows PC, over Wi-Fi or USB.",
                modifier = Modifier.padding(start = 16.dp, end = 16.dp, bottom = 16.dp),
            )
        }
        Section("Made by") {
            Text("Omar Kharrat", fontWeight = FontWeight.Medium)
            Row {
                TextButton(onClick = { uri.openUri(GITHUB_URL) }) { Text("GitHub") }
                TextButton(onClick = { uri.openUri(LINKEDIN_URL) }) { Text("LinkedIn") }
            }
            Text("Open source under the MIT license.", style = MaterialTheme.typography.bodySmall)
        }
        Section("Updates") {
            if (!Updater.enabled) {
                Text("Debug build: updates are off.", style = MaterialTheme.typography.bodySmall)
                return@Section
            }
            val status = when (update) {
                Updater.State.Checking -> "Checking for updates…"
                Updater.State.UpToDate -> "You have the latest version."
                is Updater.State.Failed -> update.message
                is Updater.State.Available -> "Version ${update.update.versionName} is available (see the Webcam page)."
                is Updater.State.Downloading -> "Downloading ${update.update.versionName}…"
                is Updater.State.Ready -> "Version ${update.update.versionName} is downloaded and ready to install."
                Updater.State.Idle -> "CamAndroid checks for updates every time it opens."
            }
            Text(
                status,
                style = MaterialTheme.typography.bodySmall,
                color = if (update is Updater.State.Failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedButton(
                onClick = {
                    if (update is Updater.State.Ready) Updater.install(context, update.file) else Updater.check(context, quiet = false)
                },
                enabled = update !is Updater.State.Checking && update !is Updater.State.Downloading,
            ) { Text(if (update is Updater.State.Ready) "Install update" else "Check for updates") }
        }
    }
}
