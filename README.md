# CamAndroid

Use your Android phone's camera as a webcam on your computer, over Wi-Fi or USB.

- **Android app** (`android/`): Kotlin, Jetpack Compose, CameraX. Runs a TLS server in a foreground
  service, so it keeps streaming with the screen off.
- **Windows desktop app** (`desktop/`): Python, packaged as a single `camandroid.exe`. Has a GUI and a CLI,
  and outputs to a virtual webcam that Zoom, Teams, Meet, OBS and other apps can select.
- **CI/CD** (`.github/workflows/ci.yml`): builds and tests both apps, runs the Python client against the
  real Kotlin server, and publishes a GitHub Release when you push a `v*` tag.

```
 Phone (CamAndroid app)                         Windows PC (camandroid.exe)
 CameraX → JPEG ─┐                            ┌─→ decode/rotate/scale → virtual webcam
                 └── TLS :4747 (Wi-Fi / USB) ─┘    (OBS Virtual Camera driver)
```

## Quick start

### 1. Install the virtual camera driver (one time)

Install [OBS Studio](https://obsproject.com/). It includes the *OBS Virtual Camera* driver that
camandroid writes to. You don't need to keep OBS running.

### 2. Install the apps

Download from the [Releases](../../releases) page (or from the artifacts of any CI run):

- `CamAndroid-<version>.apk`: install it on the phone. Allow "install unknown apps" if prompted.
- `camandroid-windows-x86_64.exe`: run it on the PC. It is unsigned, so SmartScreen may warn you;
  choose *More info → Run anyway*. Allow it through Windows Firewall on **private** networks when asked.

The desktop app can also be installed from source with Python 3.10+: `pip install ./desktop` and then run `camandroid`.

### 3. Pair (one time)

1. On the phone, open **CamAndroid**, tap **Start** and allow camera access.
2. Tap **Pair new computer**. A code like `K7QM-4X2P-9RTD` appears.
3. On the computer, open **camandroid**, select the phone (it is found automatically on the same Wi-Fi
   network), click **Pair…** and type the code.

If discovery is blocked (guest Wi-Fi, firewall), use **Pair by address…** with the IP shown in the app.

### 4. Stream

Select the phone and click **Start webcam**. In your video app, pick **OBS Virtual Camera**.

You can change the camera, flashlight, quality, rotation, mirroring and crop-to-fill while streaming.
To change resolution or frame rate, stop the webcam first.

### USB instead of Wi-Fi

USB gives lower latency and no Wi-Fi congestion. Enable *Developer options → USB debugging* on the
phone, install [platform-tools](https://developer.android.com/tools/releases/platform-tools) (`adb`)
on the PC, plug in the cable and tick **Connect over USB**. The desktop app runs
`adb forward` for you. Pairing works the same way over USB.

## CLI

```
camandroid                       # open the GUI
camandroid discover              # list phones on the network
camandroid pair 192.168.1.20     # pair (prompts for the code)
camandroid pair --usb            # pair over USB
camandroid devices               # list paired phones
camandroid stream                # stream the only paired phone to the virtual webcam
camandroid stream --usb --size 1920x1080 --fps 30 --camera front --mirror
camandroid probe 192.168.1.20    # TLS version, cipher, certificate fingerprint
camandroid forget "Pixel 8"
```

The packaged `.exe` is a windowed app, so CLI output is only visible when installed with `pip install ./desktop`.

## Security

All video is encrypted with TLS. Only paired computers can connect. A pairing code works once and
expires after 5 minutes. The computer pins the phone's certificate fingerprint, so if someone
impersonates the phone later, the connection fails loudly. Secrets on the phone are encrypted with the Android
Keystore and excluded from backups. The full design and threat table are in
[docs/PROTOCOL.md](docs/PROTOCOL.md).

## Development

```
# Android (needs JDK 17 and the Android SDK; or just let CI build it)
cd android && ./gradlew :core:test :app:assembleDebug

# Run the real phone server with a synthetic camera (no phone needed)
cd android && ./gradlew :core:runFakePhone -PfakePhoneArgs="4747 /tmp/code.txt"

# Desktop
cd desktop && pip install -e ".[dev]" && ruff check . && pytest
camandroid pair 127.0.0.1 --code "$(cat /tmp/code.txt)" && camandroid stream 127.0.0.1 --no-vcam
```

The protocol and pairing logic live in the pure Kotlin `android/core` module, which is unit tested on
the JVM. The Android app module adds only the UI, the camera and the system integration.

## CI/CD

Every push and pull request runs:

| Job | What it does |
|---|---|
| Android app | Core unit tests, Android lint, unit tests, debug and release APK builds (uploaded as artifacts) |
| Desktop (Windows) | ruff and pytest, then a PyInstaller build of `camandroid-windows-x86_64.exe` (uploaded as an artifact) |
| Interop | Starts the Kotlin server with a fake camera and runs the Python client against it |
| Release | On a `v*` tag, publishes a GitHub Release with the APK, the Windows exe and SHA256SUMS |

To cut a release: `git tag v0.1.0 && git push origin v0.1.0`.

### Release signing

Without signing secrets, CI produces a signed **debug** APK, which you can install, and an **unsigned** release
APK. To get a signed release APK, create a keystore once:

```
keytool -genkeypair -v -keystore release.jks -keyalg RSA -keysize 4096 -validity 10000 -alias camandroid
# PowerShell: copy the keystore as base64 to the clipboard
[Convert]::ToBase64String([IO.File]::ReadAllBytes("release.jks")) | Set-Clipboard
```

Then add these repository secrets (*Settings → Secrets and variables → Actions*):
`ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`.
Keep `release.jks` backed up somewhere safe: updates must be signed with the same key.

## Troubleshooting

- **Phone not discovered**: some routers block mDNS between clients. Use *Pair by address* with the IP
  shown in the app, or use USB.
- **"Could not open a virtual camera"**: OBS Studio from step 1 is not installed, or another app is
  already using OBS Virtual Camera. Stop OBS's own virtual camera if it is running.
- **Fingerprint mismatch**: you reinstalled the app or cleared its data, which creates a new identity.
  Click *Forget* and pair again. If you did neither, treat it as a possible interception.
- **Choppy video**: lower the quality or resolution, use 5 GHz Wi-Fi, or switch to USB.
