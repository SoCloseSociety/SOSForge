#!/usr/bin/env bash
# Check a release APK of the SOSForge native app before it leaves the machine
# (mirrors EBOOKFORGE/mobile-native/tools/check-release.sh, itself from KontentReach).
#
#   tools/check-release.sh <app.apk>
#   tools/check-release.sh --elf <lib.so>     one library: 16 KB page aligned?
#
# Checks, each printed PASS / FAIL, exit 1 when any fails:
#   1. targetSdk   >= 36 (Android 16)
#   2. version     versionCode > the last one shipped (dist/.last-version-code), versionName X.Y.Z
#   3. permissions an ALLOWLIST: anything a library merges in is a FAIL until reviewed and added here
#   4. 16 KB       every 64-bit .so (MapLibre ships them) has 16 KB aligned LOAD segments, and the APK
#                  passes zipalign -c -P 16
#   5. signer      not the debug key; CN=SOSForge; the certificate SHA-256 equals the one recorded when
#                  the key was made (~/.soclose/android/sosforge.cert.sha256). The keystore is never
#                  opened here: no password ever reaches a command line.
#   6. hosts       the release names https://sosforge.soclose.co and NO development host at all: no
#                  127.0.0.1, 10.0.2.2, 10.8.0.1, no drill port or word (owner rule, 2026-10-27: nothing
#                  fake, a release talks to production only; the override and its hosts are debug-only)
#   7. network     the shipped network_security_config permits no cleartext, and the server editor's
#                  strings are absent from the release resources (no override screen shipped)
set -uo pipefail

MIN_TARGET_SDK=36
APP_ID="co.soclose.sosforge"
ALLOWED_PERMISSIONS="android.permission.INTERNET android.permission.ACCESS_NETWORK_STATE android.permission.POST_NOTIFICATIONS android.permission.VIBRATE android.permission.FOREGROUND_SERVICE android.permission.FOREGROUND_SERVICE_SPECIAL_USE android.permission.RECEIVE_BOOT_COMPLETED android.permission.ACCESS_COARSE_LOCATION android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS android.permission.WAKE_LOCK"
DEV_URLS="http://localhost 127.0.0.1 10.0.2.2 10.8.0.1 :8300 :5273 :8380 :8334 :8360 drill DRILL"
PAGE=16384
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERT_FILE="$HOME/.soclose/android/sosforge.cert.sha256"
FAILED=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; FAILED=1; }
note() { printf '      %s\n' "$*"; }

elf_check() {
  python3 -I - "$1" "$PAGE" <<'PY'
import struct, sys
path, page = sys.argv[1], int(sys.argv[2])
data = open(path, 'rb').read()
if data[:4] != b'\x7fELF': print('skip not-elf'); sys.exit(0)
if data[4] != 2: print('skip elf32'); sys.exit(0)
end = '<' if data[5] == 1 else '>'
phoff, = struct.unpack_from(end + 'Q', data, 32)
phentsize, phnum = struct.unpack_from(end + 'HH', data, 54)
worst = None
for i in range(phnum):
    off = phoff + i * phentsize
    if struct.unpack_from(end + 'I', data, off)[0] != 1: continue
    align, = struct.unpack_from(end + 'Q', data, off + 48)
    worst = align if worst is None else min(worst, align)
if worst is None: print('skip no-load'); sys.exit(0)
print(('ok ' if worst >= page else 'bad ') + hex(worst))
sys.exit(0 if worst >= page else 1)
PY
}

if [ "${1:-}" = "--elf" ]; then out="$(elf_check "$2")"; rc=$?; echo "$2: $out"; exit $rc; fi
APK="${1:-}"
[ -n "$APK" ] && [ -f "$APK" ] || { echo "usage: $0 <app.apk> | --elf <lib.so>" >&2; exit 2; }
case "$APK" in /*) ;; *) APK="$(cd "$(dirname "$APK")" && pwd)/$(basename "$APK")" ;; esac
echo "artifact: $APK"
SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
BT="$(ls -d "$SDK"/build-tools/* 2>/dev/null | sort -V | tail -1)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

"$BT/aapt2" dump badging "$APK" > "$TMP/badging" 2>/dev/null || fail "aapt2 could not read the APK"
TARGET="$(sed -n "s/^targetSdkVersion:'\([0-9]*\)'.*/\1/p" "$TMP/badging")"
CODE="$(sed -n "s/^package:.* versionCode='\([0-9]*\)'.*/\1/p" "$TMP/badging")"
NAME="$(sed -n "s/^package:.* versionName='\([^']*\)'.*/\1/p" "$TMP/badging")"
PKG="$(sed -n "s/^package: name='\([^']*\)'.*/\1/p" "$TMP/badging")"
PERMS="$(sed -n "s/^uses-permission: name='\([^']*\)'.*/\1/p" "$TMP/badging" | sort -u)"

[ "$PKG" = "$APP_ID" ] && pass "package $PKG" || fail "package '$PKG' is not $APP_ID (a debug build?)"
[ -n "$TARGET" ] && [ "$TARGET" -ge "$MIN_TARGET_SDK" ] && pass "targetSdk $TARGET" || fail "targetSdk '$TARGET' < $MIN_TARGET_SDK"
LAST="$(cat "$ROOT/dist/.last-version-code" 2>/dev/null || echo 0)"
if [ -n "$CODE" ] && [ "$CODE" -gt "$LAST" ]; then pass "versionCode $CODE (> $LAST shipped)"
elif [ -n "$CODE" ] && [ "$CODE" -eq "$LAST" ] && [ "${REBUILD_SAME_CODE:-0}" = "1" ]; then pass "versionCode $CODE rebuilt (REBUILD_SAME_CODE: this code never left the machine)"
else fail "versionCode '$CODE' is not above the last shipped ($LAST)"; fi
printf '%s' "$NAME" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' && pass "versionName $NAME" || fail "versionName '$NAME' is not X.Y.Z"

bad=""
for p in $PERMS; do
  [ "$p" = "$APP_ID.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION" ] && continue
  case " $ALLOWED_PERMISSIONS " in *" $p "*) ;; *) bad="$bad $p" ;; esac
done
[ -z "$bad" ] && pass "permissions: $(echo $PERMS | tr '\n' ' ')" || fail "permissions not on the allowlist:$bad"

LIBS="$(unzip -Z1 "$APK" | grep -E '^lib/(arm64-v8a|x86_64)/.*\.so$' || true)"
if [ -z "$LIBS" ]; then fail "no 64-bit native library found (MapLibre should ship some)"; else
  (cd "$TMP" && unzip -q -o "$APK" $LIBS -d libs)
  mis=""; n=0
  for l in $LIBS; do out="$(elf_check "$TMP/libs/$l")"; case "$out" in ok*) n=$((n+1)) ;; bad*) mis="$mis $l:${out#bad }" ;; esac; done
  [ -z "$mis" ] && [ "$n" -gt 0 ] && pass "16 KB: $n 64-bit libraries aligned to >= $PAGE" || fail "16 KB: misaligned:$mis"
  "$BT/zipalign" -c -P 16 4 "$APK" >/dev/null 2>&1 && pass "zipalign -c -P 16: libraries on 16 KB boundaries" || fail "zipalign -c -P 16 4 failed"
fi

if "$BT/apksigner" verify --print-certs "$APK" > "$TMP/certs" 2>&1; then
  DN="$(sed -n 's/^Signer #1 certificate DN: //p' "$TMP/certs" | head -1)"
  SHA="$(sed -n 's/^Signer #1 certificate SHA-256 digest: //p' "$TMP/certs" | head -1 | tr -d ': ' | tr 'A-F' 'a-f')"
  case "$DN" in *"Android Debug"*) fail "signed with the DEBUG key" ;; *CN=SOSForge*) pass "signer: $DN" ;; *) fail "signer is not CN=SOSForge: $DN" ;; esac
  note "certificate SHA-256: $SHA"
  if [ -f "$CERT_FILE" ]; then
    [ "$(tr -d ': \n' < "$CERT_FILE" | tr 'A-F' 'a-f')" = "$SHA" ] && pass "signer matches $CERT_FILE" || fail "signer does not match $CERT_FILE"
  else fail "no recorded certificate ($CERT_FILE): the signer was not compared"; fi
else fail "apksigner verify failed: $(head -2 "$TMP/certs" | tr '\n' ' ')"; fi

unzip -p "$APK" 'classes*.dex' > "$TMP/dex" 2>/dev/null
found=""
for u in $DEV_URLS; do LC_ALL=C grep -aqF -- "$u" "$TMP/dex" && found="$found $u"; done
LC_ALL=C grep -aqF "https://sosforge.soclose.co" "$TMP/dex" || found="$found (default origin missing)"
[ -z "$found" ] && pass "hosts: https://sosforge.soclose.co, no development host, no drill" || fail "hosts:$found"

"$BT/aapt2" dump resources "$APK" > "$TMP/res" 2>/dev/null
NSC="$(grep -A3 'xml/network_security_config' "$TMP/res" | sed -n 's/.*(\(res\/[^)]*\)).*/\1/p; s/.*\[\(res\/[^]]*\)\].*/\1/p' | head -1)"
[ -z "$NSC" ] && NSC="$(grep -A3 'xml/network_security_config' "$TMP/res" | grep -o 'res/[^ )"]*' | head -1)"
if [ -n "$NSC" ] && "$BT/aapt2" dump xmltree --file "$NSC" "$APK" > "$TMP/nsc" 2>/dev/null; then
  if grep -q 'cleartextTrafficPermitted.*true\|cleartextTrafficPermitted.*0xffffffff' "$TMP/nsc"; then fail "network: the shipped network config permits cleartext ($NSC)"
  elif grep -q 'domain' "$TMP/nsc"; then fail "network: the shipped network config lists a domain ($NSC)"
  else pass "network: no cleartext anywhere ($NSC)"; fi
else fail "network: network_security_config not found in the APK"; fi
editor=""
for k in set_server_hint set_server_bad set_server_default; do grep -q "string/$k" "$TMP/res" && editor="$editor $k"; done
[ -z "$editor" ] && pass "no server override screen (its strings are not in the release)" || fail "the server editor shipped:$editor"

echo
[ "$FAILED" -eq 0 ] && echo "RESULT  ready to ship" || echo "RESULT  NOT ready (see FAIL lines)"
exit "$FAILED"
