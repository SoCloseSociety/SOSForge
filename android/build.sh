#!/usr/bin/env bash
# SOSForge native app: unit tests + lint, then a signed release APK into dist/, then the release gate.
# (Shape of GSTATSFORGE/android/build.sh 0.2.4, gate of EBOOKFORGE/mobile-native/tools/check-release.sh.)
#  - the release key lives OUTSIDE the repo: ~/.soclose/android/sosforge.properties (+ sosforge-release.jks).
#    If it is absent it is created here, once, with a random password written ONLY into that properties
#    file (chmod 600); the password never reaches a command line (keytool reads it from the environment);
#  - the build refuses to ship without it (no silent debug-signed release);
#  - versionCode must be above the last one built (never reuse a versionCode that left the machine);
#  - the Mac is shared: one Gradle at a time, no daemon, and we wait while the machine is loaded.
# Needs JDK 21 (JDK 24 breaks the native toolchain: EbookForge lesson) and the Android SDK (platform 36).
set -euo pipefail
cd "$(dirname "$0")"
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home}"
KEYDIR="$HOME/.soclose/android"
PROPS="$KEYDIR/sosforge.properties"
JKS="$KEYDIR/sosforge-release.jks"
CERT="$KEYDIR/sosforge.cert.sha256"

if [ ! -f "$PROPS" ]; then
  [ -f "$JKS" ] && { echo "$JKS exists without $PROPS: refusing to overwrite a key" >&2; exit 1; }
  mkdir -p "$KEYDIR" && chmod 700 "$KEYDIR"
  SOS_KEY_PASS="$(openssl rand -base64 30 | tr -d '/+=\n' | cut -c1-32)"
  export SOS_KEY_PASS
  "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$JKS" -storetype PKCS12 -alias sosforge \
    -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=SOSForge, O=SoClose Pte. Ltd., C=SG" \
    -storepass:env SOS_KEY_PASS -keypass:env SOS_KEY_PASS >/dev/null
  umask 077
  printf 'storeFile=%s\nstorePassword=%s\nkeyAlias=sosforge\nkeyPassword=%s\n' "$JKS" "$SOS_KEY_PASS" "$SOS_KEY_PASS" > "$PROPS"
  chmod 600 "$PROPS" "$JKS"
  "$JAVA_HOME/bin/keytool" -J-Duser.language=en -list -v -keystore "$JKS" -storepass:env SOS_KEY_PASS -alias sosforge \
    | sed -n 's/^[[:space:]]*SHA256: //p' | head -1 | tr -d ': ' | tr 'A-F' 'a-f' > "$CERT"
  unset SOS_KEY_PASS
  echo "created the release key $JKS (certificate sha256 in $CERT)"
fi

CODE=$(grep '^versionCode=' version.properties | cut -d= -f2)
NAME=$(grep '^versionName=' version.properties | cut -d= -f2)
mkdir -p dist
LAST=$(cat dist/.last-version-code 2>/dev/null || echo 0)
if [ "$CODE" -le "$LAST" ] && [ "${REBUILD_SAME_CODE:-0}" != "1" ]; then
  echo "versionCode $CODE is not above the last build ($LAST): bump version.properties" >&2
  exit 1
fi

# Memory-pressure guard (the owner's Mac froze once from too many processes).
for _ in 1 2 3 4 5 6; do
  LOAD=$(uptime | sed 's/.*load averages*: *//' | cut -d' ' -f1 | tr -d ',')
  FREE_MB=$(sysctl -n vm.swapusage | sed 's/.*free = \([0-9.]*\)M.*/\1/' | cut -d. -f1)
  if [ "${FREE_MB:-0}" -ge 700 ] && [ "$(printf '%.0f' "$LOAD")" -le 20 ]; then break; fi
  echo "machine busy (load $LOAD, free swap ${FREE_MB} MB): waiting 60 s" >&2
  sleep 60
done

# The unit tests of the RELEASE variant (the one shipped), its lint, then the signed APK.
./gradlew --no-daemon testReleaseUnitTest lintRelease assembleRelease

APK="dist/sosforge-v$NAME.apk"
cp app/build/outputs/apk/release/app-release.apk "$APK"
tools/check-release.sh "$APK"
echo "$CODE" > dist/.last-version-code
shasum -a 256 "$APK" | tee "$APK.sha256"
echo "OK sosforge-v$NAME (code $CODE)"
