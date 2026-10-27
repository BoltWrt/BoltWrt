#!/bin/sh
# Local post-upgrade evidence only; no reload, restart, repair or flash.
set -eu
export LC_ALL=C
metadata() { ls -ldn "$1" | awk '{print $1, $3, $4}'; }
umask 077
fail() { echo "UPGRADE_FIXTURE_FAILED: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'root required in the disposable guest'
intent=${1:-}
case "$intent" in enabled|disabled) ;; *) fail 'usage: verify-upgrade-fixture.sh enabled|disabled';; esac
. /etc/openwrt_release
[ "$DISTRIB_ID/$DISTRIB_RELEASE/$DISTRIB_TARGET" = 'BoltWrt/4.0.0/x86/64' ] || fail 'requires upgraded BoltWrt 4.0.0 x86/64'
root=/root/boltwrt-upgrade-fixture
[ -d "$root" ] && [ ! -L "$root" ] || fail 'fixture directory absent or replaced'
for name in probe.sh test.crt test.key intent certificate-der.sha256 sha256sums metadata; do
    [ -f "$root/$name" ] && [ ! -L "$root/$name" ] || fail "missing/invalid fixture file: $name"
done
[ "$(cat "$root/intent")" = "$intent" ] || fail 'expected intent differs from preserved fixture'
sha256sum -c "$root/sha256sums" >/dev/null 2>&1 || fail 'preserved file digest mismatch'
while read -r mode uid gid path extra; do
    [ -z "${extra:-}" ] || fail 'malformed metadata'
    case "$path" in "$root/"*|/etc/haproxy.cfg) ;; *) fail 'unexpected metadata path';; esac
    [ -f "$path" ] && [ ! -L "$path" ] || fail 'metadata member is not a regular file'
    [ "$(metadata "$path")" = "$mode $uid $gid" ] || fail "mode/owner mismatch: $path"
done < "$root/metadata"
[ "$(metadata "$root/metadata")" = '-rw------- 0 0' ] || fail 'metadata permissions changed'
[ ! -e /usr/local/boltwrt-upgrade-negative-control ] && [ ! -L /usr/local/boltwrt-upgrade-negative-control ] || fail 'unregistered negative control survived'
expect() { [ "$(uci get "$1")" = "$2" ] || fail "retained UCI mismatch: $1"; }
expect network.lan.proto static
expect network.lan.ipaddr 10.126.0.1
expect network.lan.netmask 255.255.255.0
expect system.@system[0].hostname "boltwrt-upgrade-$intent"
expect system.ntp.enabled 0
expect system.ntp.server time.fixture.invalid
expect ddns.boltwrt_upgrade_fixture service
expect ddns.boltwrt_upgrade_fixture.enabled 0
expect ddns.boltwrt_upgrade_fixture.domain fixture.example.invalid
expect ddns.boltwrt_upgrade_fixture.lookup_host fixture.example.invalid
expect ddns.boltwrt_upgrade_fixture.service_name cloudflare.com-v4
for field in username password; do
    if uci -q get "ddns.boltwrt_upgrade_fixture.$field" >/dev/null; then fail 'unexpected fixture DDNS credentials'; fi
done
expect sqm.boltwrt_upgrade_fixture queue
expect sqm.boltwrt_upgrade_fixture.enabled 0
expect sqm.boltwrt_upgrade_fixture.interface br-lan
expect sqm.boltwrt_upgrade_fixture.download 12345
expect sqm.boltwrt_upgrade_fixture.upload 2345
expect sqm.boltwrt_upgrade_fixture.qdisc cake
expect sqm.boltwrt_upgrade_fixture.script piece_of_cake.qos
expect sqm.boltwrt_upgrade_fixture.use_mq 1
expect nginx._lan.ssl_certificate "$root/test.crt"
expect nginx._lan.ssl_certificate_key "$root/test.key"
if uci -q get nginx._lan.uci_manage_ssl >/dev/null; then fail 'fixture certificate management unexpectedly reintroduced'; fi
for script in 09-boltwrt-haproxy 10_disable_services; do
    [ ! -e "/etc/uci-defaults/$script" ] && [ ! -L "/etc/uci-defaults/$script" ] || fail "unprocessed uci-defaults script: $script"
done
[ -f /rom/etc/uci-defaults/09-boltwrt-haproxy ] || fail 'target 09 hook absent from image'
[ -x /etc/init.d/haproxy ] || fail 'target haproxy init absent'
if /etc/init.d/haproxy enabled; then actual=enabled; else actual=disabled; fi
[ "$actual" = "$intent" ] || fail 'haproxy enabled intent changed'
if [ "$intent" = disabled ]; then
    for link in /etc/rc.d/S??haproxy /etc/rc.d/K??haproxy; do
        [ ! -e "$link" ] && [ ! -L "$link" ] || fail 'disabled haproxy has rc links'
    done
fi
haproxy -c -q -f /etc/haproxy.cfg >/dev/null 2>&1 || fail 'retained haproxy config cannot be parsed'
[ ! -e /usr/lib/opkg/status ] && [ ! -L /usr/lib/opkg/status ] || fail 'old opkg database restored'
[ ! -d /usr/lib/opkg/info ] || fail 'old opkg package metadata restored'
if command -v opkg >/dev/null 2>&1; then fail 'old opkg executable exists'; fi
command -v apk >/dev/null || fail 'APK executable absent'
[ -s /lib/apk/db/installed ] || fail 'APK installed database absent'
apk --version
apk info -e base-files >/dev/null || fail 'APK installed package query failed'

# Use the actual UCI-generated configuration, never nginx's compiled default.
conf=/etc/nginx/uci.conf
[ -f "$conf" ] || fail 'generated nginx UCI configuration absent'
[ -s /var/run/nginx.pid ] || fail 'nginx master pidfile absent'
pid=$(cat /var/run/nginx.pid)
case "$pid" in ''|*[!0-9]*) fail 'invalid nginx master PID';; esac
[ -r "/proc/$pid/cmdline" ] || fail 'nginx master not running'
cmd=$(tr '\000' ' ' < "/proc/$pid/cmdline")
case "$cmd" in "nginx: master process /usr/sbin/nginx -c $conf -g "*) ;; *) fail 'nginx master uses an unexpected config';; esac
tmp=$(mktemp -d /tmp/boltwrt-upgrade-verify.XXXXXX)
nginx -T -c "$conf" > "$tmp/nginx-config.private" 2> "$tmp/nginx-test.log" || fail 'actual nginx configuration test failed'
grep -F "ssl_certificate $root/test.crt;" "$tmp/nginx-config.private" >/dev/null || fail 'generated nginx config does not reference fixture certificate'
grep -F "ssl_certificate_key $root/test.key;" "$tmp/nginx-config.private" >/dev/null || fail 'generated nginx config does not reference fixture private key'
openssl x509 -in "$root/test.crt" -outform DER > "$tmp/certificate.der" || fail 'retained certificate cannot be parsed'
[ "$(sha256sum "$tmp/certificate.der" | awk '{print $1}')" = "$(cat "$root/certificate-der.sha256")" ] || fail 'certificate identity changed'
echo "UPGRADE_FIXTURE_LOCAL_CHECKS_PASSED: intent=$intent evidence=$tmp"
echo 'HTTPS_SERVED_CERTIFICATE_HANDSHAKE=PENDING_EXTERNAL_ROOT_CHECK'
echo 'Run these local checks again after a real guest reboot. Shell success alone is not full upgrade/HTTPS acceptance.'
