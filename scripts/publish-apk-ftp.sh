#!/usr/bin/env bash
# Uploads a release APK and version.json to the update server, which the Android app's update
# checker reads (android/app/src/main/kotlin/com/camandroid/app/Updater.kt).
#
# Usage: scripts/publish-apk-ftp.sh path/to/CamAndroid-x.y.z.apk [release-notes-url]
# Env:   FTP_PASSWORD (secret; the script does nothing without it)
#        FTP_HOST, FTP_USER, FTP_DIR, PUBLIC_URL (defaults below)
set -euo pipefail

apk=$1
notes=${2:-https://github.com/smayah69/CamAndroid/releases}
FTP_HOST=${FTP_HOST:-bombo.cu.ma}
FTP_USER=${FTP_USER:-omar@bombo.cu.ma}
FTP_DIR=${FTP_DIR:-public_html/extras/camandroid}
PUBLIC_URL=${PUBLIC_URL:-https://bombo.cu.ma/extras/camandroid}

if [ -z "${FTP_PASSWORD:-}" ]; then
  echo "::notice::FTP_PASSWORD secret not set; skipping the upload to $FTP_HOST"
  exit 0
fi

# Read the version from the APK itself, so this works for any release.
aapt2=$(ls -d "$ANDROID_HOME"/build-tools/*/ | sort -V | tail -1)aapt2
badging=$("$aapt2" dump badging "$apk")
package=$(sed -n "s/^package: name='\([^']*\)'.*/\1/p" <<<"$badging")
code=$(sed -n "s/^package: .*versionCode='\([0-9]*\)'.*/\1/p" <<<"$badging")
name=$(sed -n "s/^package: .*versionName='\([^']*\)'.*/\1/p" <<<"$badging")
if [ "$package" != "com.camandroid.app" ] || [ -z "$code" ] || [ -z "$name" ]; then
  echo "::error::$apk is not the CamAndroid release APK (package '$package', version '$name' ($code))"
  exit 1
fi
file="CamAndroid-$name.apk"
sha=$(sha256sum "$apk" | cut -d' ' -f1)
size=$(stat -c %s "$apk")

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
jq -n --arg name "$name" --argjson code "$code" --arg url "$PUBLIC_URL/$file" --arg sha "$sha" \
  --argjson size "$size" --arg notes "$notes" --arg released "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{versionName: $name, versionCode: $code, url: $url, sha256: $sha, size: $size, minSdk: 26,
    released: $released, notes: $notes}' > "$work/version.json"
cat "$work/version.json"

# Credentials go through a curl config file, not the command line (visible in the process list).
umask 077
quoted=$(printf '%s:%s' "$FTP_USER" "$FTP_PASSWORD" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
printf 'user = "%s"\n' "$quoted" > "$work/auth"
base="ftp://$FTP_HOST/$FTP_DIR"

# Prefer FTPS with a valid certificate. Shared hosts often present the server's own certificate,
# so fall back to TLS without verification. Plain FTP (password in clear text) only when the
# repository variable FTP_ALLOW_PLAIN is "true".
modes=("--ssl-reqd" "--ssl-reqd --insecure")
[ "${FTP_ALLOW_PLAIN:-}" = "true" ] && modes+=("plain")
tls=""
connected=""
for mode in "${modes[@]}"; do
  opts=$mode
  [ "$mode" = "plain" ] && opts=""
  # shellcheck disable=SC2086
  if curl -sS -K "$work/auth" $opts --list-only "ftp://$FTP_HOST/" -o /dev/null 2> "$work/err"; then
    tls=$opts
    connected=$mode
    break
  fi
  echo "FTP login with '$mode' failed: $(cat "$work/err")"
done
case "$connected" in
  "--ssl-reqd") echo "Connected with FTPS (certificate verified)" ;;
  "--ssl-reqd --insecure") echo "::warning::$FTP_HOST: FTPS certificate is not valid for this host name; using TLS without verification" ;;
  "plain") echo "::warning::$FTP_HOST does not offer FTPS; the password was sent in clear text" ;;
  *)
    echo "::error::could not log in to $FTP_HOST as $FTP_USER over FTPS (check the FTP_PASSWORD secret; set the repository variable FTP_ALLOW_PLAIN=true only if the server has no TLS)"
    exit 1
    ;;
esac

ftp() {
  # shellcheck disable=SC2086
  curl -sS --fail -K "$work/auth" $tls --ftp-create-dirs --retry 3 "$@"
}

# APK first, then version.json, so the app never sees a version whose file is missing.
ftp -T "$apk" "$base/$file"
ftp -T "$work/version.json" "$base/version.json"

# Keep this APK and the previous one (someone may be downloading it right now).
ftp --list-only "$base/" > "$work/listing"
mapfile -t old < <(tr -d '\r' < "$work/listing" | grep -E '^CamAndroid-.*\.apk$' | grep -vxF "$file" | sort -V | head -n -1 || true)
for f in "${old[@]}"; do
  echo "Deleting old $f"
  ftp -Q "DELE $FTP_DIR/$f" "ftp://$FTP_HOST/" -o /dev/null || echo "::warning::could not delete $f"
done

# Check what the app will download.
sleep 2
live=$(curl -fsS -H "Cache-Control: no-cache" "$PUBLIC_URL/version.json?t=$(date +%s)")
echo "Live version.json: $live"
[ "$(jq -r .sha256 <<<"$live")" = "$sha" ] || { echo "::error::$PUBLIC_URL/version.json does not show the new upload"; exit 1; }
got=$(curl -fsSL "$PUBLIC_URL/$file" | sha256sum | cut -d' ' -f1)
[ "$got" = "$sha" ] || { echo "::error::$PUBLIC_URL/$file does not match the APK"; exit 1; }
echo "Published CamAndroid $name ($code) at $PUBLIC_URL/$file"
