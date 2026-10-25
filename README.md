# CamAndroid

Use your Android phone's camera as a webcam on your computer, over Wi-Fi or USB.

- **Android app** (`android/`): Kotlin, Jetpack Compose, CameraX. Runs a TLS server in a foreground
  service, so it keeps streaming with the screen off.
- **Windows desktop app** (`desktop/`): Python, packaged as `camandroid.exe`, with a GUI and a CLI.
- **CamAndroid virtual camera** (`windows/vcam/`): our own C++ DirectShow camera DLL (64- and 32-bit).
  It shows up as **CamAndroid** in Zoom, Teams, Google Meet/Chrome/Edge, Discord, Skype and OBS.
  OBS is not needed.
- **CI/CD** (`.github/workflows/ci.yml`): builds and tests everything, tests the camera with real
  DirectShow clients on Windows, builds the installer, and publishes a GitHub Release when you push a
  `v*` tag.

```
 Phone (CamAndroid app)                       Windows PC
 CameraX → H.264 ┐                          ┌─ camandroid.exe ── shared memory ──┐
 (hardware)      └─ TLS :4747 ──────────────┘   decode/rotate/scale             │
                    Wi-Fi, or USB via the                                        ▼
                    bundled adb             Zoom / Teams / Chrome ◄─ CamAndroid camera DLL
```

## Quick start

### 1. Install

Download the newest release from the [Releases](../../releases) page. Windows builds are signed
through [SignPath Foundation](https://signpath.org) (see [Code signing policy](#code-signing-policy)). A new one is published
automatically for every update:

- **Phone:** `CamAndroid-<version>.apk`. Allow "install unknown apps" if prompted.
- **PC:** `CamAndroid-Setup-<version>.exe`. This installs the app, the **CamAndroid** camera for all
  users and the USB tools (adb), and allows phone discovery on private networks. Nothing else is needed.

There is also `CamAndroid-<version>-portable.exe`, which needs no installer and no admin rights. On
first start it offers to install the camera for your Windows user only.

Until the Windows app is signed and has built up reputation (see [Signing](#signing)), Windows
SmartScreen may warn about an unknown publisher. Choose *More info → Run anyway*.

### 2. Pair (one time)

1. On the phone, open **CamAndroid**, tap **Start** and allow camera access.
2. Tap **Pair new computer**. A code like `K7QM-4X2P-9RTD` appears.
3. On the PC, open **CamAndroid**, select the phone (it is found automatically on the same Wi-Fi), click
   **Pair…** and type the code.

If discovery is blocked (guest Wi-Fi, client isolation), use **Pair by address…** with the IP shown in the app.

### 3. Stream

Select the phone and click **Start webcam**. In Zoom, Teams, Meet or any other video app, choose
**CamAndroid** as the camera. While the phone isn't sending, the camera shows a "Waiting for the
phone…" picture, so apps never see a broken device.

You can change the camera, flashlight, quality, rotation, mirroring (on by default) and crop-to-fill
while streaming. To change resolution or frame rate, stop the webcam first.

The phone encodes **H.264 in hardware** and holds the camera at a fixed frame rate, so 60 fps works
at 720p and 1080p on phones whose camera supports 60 fps (most recent ones do; others give 30).
720p60 needs about 6 Mbit/s. The PC decodes it with Windows' built-in H.264 decoder (Media Foundation),
so the app stays small and needs nothing extra. The status bar shows the real frame rate and bitrate. In video apps,
choose a 60 fps format if the app lets you (OBS, Discord); many apps such as Teams and Meet use 30.

The **Logs** tab shows what the app is doing (also saved to `%APPDATA%\CamAndroid\logs`). The
**About** tab checks GitHub for a newer release and, for the installed app, downloads and runs the
new installer (the download's SHA-256 is checked against GitHub's).

### USB

USB has lower latency and doesn't depend on Wi-Fi. `adb` comes with CamAndroid, so you don't need to install
anything on the PC.

1. On the phone, enable *Developer options* (tap *Settings → About phone → Build number* 7 times),
   then turn on *USB debugging*.
2. Plug in the cable and accept *Allow USB debugging?* on the phone (tick *Always allow*).
3. The phone appears in the PC app's list with connection **USB**. Select it, then pair or start as usual.

Most phones work with Windows' built-in USB driver. If `adb` can't see a Samsung, Xiaomi or other
brand phone, install that manufacturer's USB driver once.

## CLI

```
camandroid                       # open the GUI
camandroid discover              # list phones on the network
camandroid pair 192.168.1.20     # pair (prompts for the code)
camandroid pair --usb            # pair over USB
camandroid devices               # list paired phones
camandroid stream                # stream the only paired phone to the virtual webcam
camandroid stream --usb --size 1920x1080 --fps 60 --camera front --no-mirror
camandroid probe 192.168.1.20    # TLS version, cipher, certificate fingerprint
camandroid forget "Pixel 8"
camandroid camera status         # is the CamAndroid camera installed?
camandroid camera install        # install it for this Windows user (no admin)
camandroid update                # check GitHub for a newer version
camandroid selftest              # check that H.264 decoding works
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

# Virtual camera (Visual Studio 2022 or Build Tools, from a developer prompt)
cmake -S windows/vcam -B build/vcam-x64 -A x64 && cmake --build build/vcam-x64 --config Release
regsvr32 /n /i:user build\vcam-x64\Release\camandroid-vcam64.dll     # per-user, no admin
build\vcam-x64\Release\vcamtest.exe --size 1280x720 --feed 200,40,120 --expect 200,40,120
```

The protocol and pairing logic live in the pure Kotlin `android/core` module, which is unit tested on
the JVM. The Android app module adds only the UI, the camera and the system integration.

## CI/CD

**Every push to `main` (the default branch) publishes a new release** on the Releases page, versioned
`v<VERSION>.<build number>` (for example `v0.3.42`), with:

- `CamAndroid-<version>.apk`: the Android app
- `CamAndroid-Setup-<version>.exe`: the Windows installer
- `CamAndroid-<version>-portable.exe`: Windows, no installation
- `SHA256SUMS.txt`, plus the change list and the APK signing-certificate fingerprint in the notes

To update, install the new APK over the old one on the phone, and run the new installer on the PC.

**Phone updates** come from `https://bombo.cu.ma/extras/camandroid/`. Each release uploads the APK and
`version.json` there over FTPS (`scripts/publish-apk-ftp.sh`); the app checks `version.json` when it
opens (and from *About › Check for updates*), downloads the APK from the same server, verifies its
SHA-256 and signing key, and opens Android's installer. Setup: add the repository secret
`FTP_PASSWORD`. Optional repository variables: `FTP_HOST` (default `bombo.cu.ma`), `FTP_USER`
(`omar@bombo.cu.ma`), `FTP_DIR` (`public_html/extras/camandroid`), and `FTP_ALLOW_PLAIN=true` if
the server has no TLS. *Actions › Publish APK to bombo.cu.ma › Run workflow* re-uploads a release
without rebuilding.
Pushing a tag such as `v1.0.0` publishes that exact version. *Actions → CI → Run workflow* rebuilds
and republishes the current default branch. Edit `VERSION` to change the major/minor number.

Every run, including pull requests and other branches, also runs these jobs:

| Job | What it does |
|---|---|
| Version | Works out the version number and whether this run publishes a release |
| Android app | Core unit tests, Android lint, unit tests, debug and release builds. The release APK is signed with `apksigner` outside Gradle, so build plugins never see the key |
| Virtual camera | Builds the 64- and 32-bit camera DLLs, registers them, and captures through real DirectShow graphs: placeholder, 640×480 to 1080p, orientation, 32-bit, the Python writer end to end, and OpenCV as an independent client |
| Windows app | ruff and pytest, signs the DLLs and exe if configured, builds `camandroid.exe` and the installer, and checks that a silent install and uninstall registers and removes the camera |
| Interop | Starts the Kotlin server with a fake camera and runs the Python client against it |
| Publish release | Only on the default branch and `v*` tags: uploads the files above as a GitHub Release |

## Signing

### Windows

The camera is a **user-mode** COM DLL loaded by video apps, not a kernel driver. Windows doesn't require
driver signing (WHQL or attestation) for it, and installing it never needs test-signing mode or
Secure Boot changes.

Authenticode signing is still worth doing: it builds SmartScreen reputation for the publisher, so the
"unknown publisher" warning goes away after enough downloads, and makes antivirus software less
suspicious. Release builds are signed through SignPath Foundation (see
[Code signing policy](#code-signing-policy)). A certificate you own also works: add its `.pfx` as
`WINDOWS_SIGNING_PFX_BASE64` (`[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx"))`)
and `WINDOWS_SIGNING_PFX_PASSWORD`. New certificates live on hardware tokens or cloud HSMs and can't
be exported as a `.pfx`, so SignPath is the practical route for CI.

A self-signed certificate doesn't help other people. It is only trusted on machines where you import it.

### Android

Android installs an update over an existing app only if both APKs are signed with **the same key**.
To get that, add **one repository secret**. You can do it from a phone browser:

1. Repository → *Settings → Secrets and variables → Actions → New repository secret*
2. Name: `ANDROID_KEYSTORE_PASSWORD`. Value: a long passphrase you keep somewhere safe (20+ random
   characters, or 5+ random words). Don't share it with anyone.
3. *Actions → CI → Run workflow* (or push any change).

On that run, CI creates a 4096-bit RSA signing key, signs the release with it and commits it to
`android/signing/release.p12`, encrypted with your passphrase (AES-256, PBKDF2 with 600,000
iterations). The passphrase is never stored in the repository. Every later release reuses the key,
and the certificate fingerprint is printed in each release's notes.

Until the secret is set, each APK is signed with a throwaway key and its release notes say so. It
installs fine, but you have to uninstall it once before installing the first permanently signed version.

To use your own keystore instead, set `ANDROID_KEYSTORE_BASE64` (the keystore, base64-encoded),
`ANDROID_KEYSTORE_PASSWORD`, and optionally `ANDROID_KEY_ALIAS` (default `camandroid`) and
`ANDROID_KEY_PASSWORD`.

## Code signing policy

Free code signing provided by [SignPath.io](https://signpath.io), certificate by
[SignPath Foundation](https://signpath.org).

- Committers and reviewers: [smayah69](https://github.com/smayah69)
- Approvers: [smayah69](https://github.com/smayah69)

Only files built from this repository by GitHub Actions are signed: `camandroid.exe`, the
CamAndroid camera DLLs and the installer. The installer also contains Google's `adb`, which keeps
Google's own signature.

Setup: the CI submits release builds to SignPath once these exist in *Settings › Secrets and
variables › Actions*: the secret `SIGNPATH_API_TOKEN` and the variable `SIGNPATH_ORGANIZATION_ID`
(optional: `SIGNPATH_PROJECT_SLUG`, default `CamAndroid`, and `SIGNPATH_SIGNING_POLICY`, default
`release-signing`). The SignPath project needs the artifact configurations `app` and `installer`
from [`.signpath/artifact-configurations`](.signpath/artifact-configurations).

### Privacy

CamAndroid sends no data anywhere except what it needs to work:

- Video goes only from your phone to the computers you paired, over your own network or USB,
  encrypted with TLS.
- The Windows app asks `api.github.com` for the latest release when it starts and when you click
  *Check for updates*, and downloads updates from `github.com` only when you click to install.
- The Android app reads `bombo.cu.ma/extras/camandroid/version.json` when it opens (at most every
  6 hours) and when you tap *Check for updates*, and downloads updates from there only when you tap
  to download.

These requests carry nothing but the app version (in the User-Agent). There are no accounts,
analytics or tracking.

## License

[MIT](LICENSE) © Omar Kharrat ([@smayah69](https://github.com/smayah69))

## Troubleshooting

- **Phone not discovered**: some routers block mDNS between clients. Use *Pair by address* with the IP
  shown in the app, or use USB.
- **CamAndroid camera missing in an app**: run `camandroid camera status` (or check the top of the
  Webcam panel), install it, then **restart the video app**, because apps list cameras when they start.
  Apps that only use the newer Media Foundation camera stack, such as the built-in Windows Camera app,
  don't list DirectShow cameras.
- **USB: "No phone found"**: check that USB debugging is on, that the cable carries data (not just
  charge), and that you accepted the prompt on the phone. A few brands need their own USB driver.
- **Fingerprint mismatch**: you reinstalled the app or cleared its data, which creates a new identity.
  Click *Forget* and pair again. If you did neither, treat it as a possible interception.
- **Choppy video**: check the status bar and the Logs tab. If it says JPEG, update the phone app
  (H.264 needs the new version on both sides). Otherwise lower the quality or resolution, use 5 GHz
  Wi-Fi, or switch to USB.
