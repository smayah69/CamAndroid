# APK signing key

`release.p12` (created automatically by CI the first time it runs with the
`ANDROID_KEYSTORE_PASSWORD` repository secret set) is the key every release APK is signed with.
Android only installs an update over an existing app when both are signed with the same key, so
keep this file and the passphrase: if either is lost, users must uninstall once to update.

The file is a PKCS#12 keystore. The private key is encrypted with AES-256, derived from the
passphrase with PBKDF2-HMAC-SHA256 (600,000 iterations). It is safe to keep in the repository only
as long as the passphrase is long and secret. Use 20+ random characters, or 5+ random words. The
passphrase itself exists only in the repository secret, never in the repository.
