package com.camandroid.app

import androidx.annotation.DrawableRes
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.res.colorResource
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** Screens reachable from the menu on the left. */
enum class Page(val label: String, @DrawableRes val icon: Int) {
    Home("Webcam", R.drawable.ic_videocam),
    Help("Help", R.drawable.ic_help),
    About("About", R.drawable.ic_info),
}

private const val GITHUB_URL = "https://github.com/smayah69/CamAndroid"
private const val LINKEDIN_URL = "https://www.linkedin.com/in/smayah/"
private val LinkedInBlue = Color(0xFF0A66C2)

/** The launcher icon (foreground on its background colour), for headers. */
@Composable
fun AppIcon(size: Dp) {
    Box(
        Modifier
            .size(size)
            .clip(RoundedCornerShape(size / 4))
            .background(colorResource(R.color.ic_launcher_background)),
    ) {
        Image(painterResource(R.drawable.ic_launcher_foreground), contentDescription = null)
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(
        text.uppercase(),
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(start = 4.dp, top = 8.dp),
    )
}

@Composable
private fun Step(number: Int, @DrawableRes icon: Int, title: String, detail: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier
                .size(44.dp)
                .clip(CircleShape)
                .background(MaterialTheme.colorScheme.primaryContainer),
            contentAlignment = Alignment.Center,
        ) {
            Icon(painterResource(icon), contentDescription = null, tint = MaterialTheme.colorScheme.onPrimaryContainer)
        }
        Spacer(Modifier.width(14.dp))
        Column {
            Text("$number. $title", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun Tile(@DrawableRes icon: Int, title: String, detail: String, modifier: Modifier) {
    Card(modifier = modifier) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(painterResource(icon), contentDescription = null, tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(28.dp))
            Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun Fact(@DrawableRes icon: Int, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 6.dp)) {
        Icon(painterResource(icon), contentDescription = null, tint = MaterialTheme.colorScheme.primary,
            modifier = Modifier.size(20.dp))
        Spacer(Modifier.width(12.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
fun HelpPage() {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SectionTitle("Get started")
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Step(1, R.drawable.ic_computer, "Get the PC app", "CamAndroid for Windows, on GitHub")
                Step(2, R.drawable.ic_link, "Pair once", "Pair new computer here, code on the PC")
                Step(3, R.drawable.ic_videocam, "Go live", "Start webcam, pick CamAndroid in Zoom…")
            }
        }

        SectionTitle("Connect with")
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Tile(R.drawable.ic_wifi, "Wi-Fi", "Same network, found automatically", Modifier.weight(1f))
            Tile(R.drawable.ic_usb, "USB", "USB debugging on, starts by itself", Modifier.weight(1f))
        }

        SectionTitle("Good to know")
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
                Fact(R.drawable.ic_lock, "Encrypted, paired computers only")
                Fact(R.drawable.ic_timer, "Stops after 2 min without a computer")
                Fact(R.drawable.ic_flip, "Selfies are mirrored, flip it on the PC")
            }
        }
    }
}

@Composable
fun AboutPage(update: Updater.State) {
    val context = LocalContext.current
    val uri = LocalUriHandler.current
    Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(
                Modifier
                    .fillMaxWidth()
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                AppIcon(72.dp)
                Spacer(Modifier.height(12.dp))
                Text("CamAndroid", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                Text("Version ${BuildConfig.VERSION_NAME}", style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(8.dp))
                Text("Your phone as a webcam for Windows, over Wi-Fi or USB.", textAlign = TextAlign.Center)
            }
        }

        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("Made by Omar Kharrat", style = MaterialTheme.typography.titleMedium)
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    FilledTonalButton(onClick = { uri.openUri(GITHUB_URL) }, modifier = Modifier.weight(1f)) {
                        Icon(painterResource(R.drawable.ic_github), contentDescription = null,
                            modifier = Modifier.size(ButtonDefaults.IconSize))
                        Spacer(Modifier.width(ButtonDefaults.IconSpacing))
                        Text("GitHub")
                    }
                    FilledTonalButton(onClick = { uri.openUri(LINKEDIN_URL) }, modifier = Modifier.weight(1f)) {
                        Icon(painterResource(R.drawable.ic_linkedin), contentDescription = null, tint = LinkedInBlue,
                            modifier = Modifier.size(ButtonDefaults.IconSize))
                        Spacer(Modifier.width(ButtonDefaults.IconSpacing))
                        Text("LinkedIn")
                    }
                }
            }
        }

        if (Updater.enabled) {
            Card(modifier = Modifier.fillMaxWidth(), colors = CardDefaults.cardColors()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Updates", style = MaterialTheme.typography.titleMedium)
                    val status = when (update) {
                        Updater.State.Checking -> "Checking…"
                        Updater.State.UpToDate -> "You have the latest version."
                        is Updater.State.Failed -> update.message
                        is Updater.State.Available -> "Version ${update.update.versionName} is available."
                        is Updater.State.Downloading -> "Downloading ${update.update.versionName}…"
                        is Updater.State.Ready -> "Version ${update.update.versionName} is ready to install."
                        Updater.State.Idle -> "Checked every time the app opens."
                    }
                    Text(
                        status,
                        style = MaterialTheme.typography.bodySmall,
                        color = if (update is Updater.State.Failed) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    OutlinedButton(
                        onClick = {
                            when (update) {
                                is Updater.State.Ready -> Updater.install(context, update.file)
                                is Updater.State.Available -> Updater.download(context, update.update)
                                else -> Updater.check(context, quiet = false)
                            }
                        },
                        enabled = update !is Updater.State.Checking && update !is Updater.State.Downloading,
                    ) {
                        Text(
                            when (update) {
                                is Updater.State.Ready -> "Install update"
                                is Updater.State.Available -> "Download update"
                                else -> "Check for updates"
                            },
                        )
                    }
                }
            }
        }

        Text(
            "Open source, MIT license",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.fillMaxWidth(),
            textAlign = TextAlign.Center,
        )
    }
}
