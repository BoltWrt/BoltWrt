#!/bin/sh
# Verify the image's own package trust, then restore one translation package.
set -eu
umask 077
package=luci-i18n-passwall2-zh-cn
archive=${1:?provide the candidate build APK for luci-i18n-passwall2-zh-cn}
[ "$(id -u)" = 0 ] && [ -f "$archive" ] && [ ! -L "$archive" ]
apk info -e "$package" >/dev/null
temporary=$(mktemp -d /tmp/boltwrt-401-apk.XXXXXX)
apk list --installed | LC_ALL=C sort > "$temporary/before-packages"
cp /etc/apk/world "$temporary/before-world"
find /etc/apk/keys -type f -exec sha256sum {} \; | LC_ALL=C sort > "$temporary/before-keys"

# No allow-untrusted option, key injection, repository access or trust changes.
apk --no-network --repositories-file /dev/null verify "$archive" > "$temporary/verify.log" 2>&1
cp "$archive" "$temporary/tampered.apk"
lua - "$temporary/tampered.apk" <<'LUA'
local f=assert(io.open(arg[1],"r+b")); assert(f:seek("end",-1)); local byte=assert(f:read(1)):byte()
assert(f:seek("end",-1)); assert(f:write(string.char((byte+1)%256))); f:close()
LUA
if apk --no-network --repositories-file /dev/null verify "$temporary/tampered.apk" > "$temporary/tampered.log" 2>&1; then
    echo 'TAMPERED_APK_ACCEPTED' >&2
    exit 1
fi
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
