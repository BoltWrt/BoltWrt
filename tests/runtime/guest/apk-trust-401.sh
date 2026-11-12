#!/bin/sh
# Verify the image's own package trust, then restore one translation package.
set -eu
umask 077
package=luci-i18n-passwall2-zh-cn
archive=${1:?provide the signed delivery APK for luci-i18n-passwall2-zh-cn}
tampered=${2:?provide the signed-metadata mutation from apk_fixture.py}
[ "$(id -u)" = 0 ] && [ -f "$archive" ] && [ ! -L "$archive" ]
[ -f "$tampered" ] && [ ! -L "$tampered" ]
[ "$(head -c 4 "$tampered")" = 'ADB.' ]
apk info -e "$package" >/dev/null
temporary=$(mktemp -d /tmp/boltwrt-401-apk.XXXXXX)
apk list --installed | LC_ALL=C sort > "$temporary/before-packages"
cp /etc/apk/world "$temporary/before-world"
find /etc/apk/keys -type f -exec sha256sum {} \; | LC_ALL=C sort > "$temporary/before-keys"

# No allow-untrusted option, key injection, repository access or trust changes.
apk --no-network --repositories-file /dev/null verify "$archive" > "$temporary/verify.log" 2>&1
cp "$tampered" "$temporary/tampered.apk"
if apk --no-network --repositories-file /dev/null verify "$temporary/tampered.apk" > "$temporary/tampered.log" 2>&1; then
    echo 'TAMPERED_APK_ACCEPTED' >&2
    exit 1
fi
grep -E 'UNTRUSTED signature|BAD signature' "$temporary/tampered.log" >/dev/null
apk --no-network --repositories-file /dev/null del "$package" > "$temporary/remove.log" 2>&1
if apk info -e "$package" >/dev/null 2>&1; then echo 'APK_REMOVAL_FAILED' >&2; exit 1; fi
apk --no-network --repositories-file /dev/null add "$archive" > "$temporary/install.log" 2>&1
apk info -e "$package" >/dev/null
apk list --installed | LC_ALL=C sort > "$temporary/after-packages"
cmp -s "$temporary/before-packages" "$temporary/after-packages"
find /etc/apk/keys -type f -exec sha256sum {} \; | LC_ALL=C sort > "$temporary/after-keys"
cmp -s "$temporary/before-keys" "$temporary/after-keys"
cp /etc/apk/world "$temporary/after-world"
echo "APK_TRUST_PASSED: signed candidate APK accepted, tampering rejected, native remove/restore completed, package versions and trust unchanged; evidence=$temporary"
