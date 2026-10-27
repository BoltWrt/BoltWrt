#!/bin/sh
# Read-only local checks in an isolated, freshly installed full-system guest.
# No configuration writes, service actions, WAN probes or peer/traffic tests.
set -eu
export LC_ALL=C
phase=preconditions
fail() { echo "FRESH_CHECK_FAILED: phase=$phase $*" >&2; exit 1; }
trap 'rc=$?; if [ "$rc" -ne 0 ]; then echo "FRESH_CHECK_INCOMPLETE: phase=$phase exit=$rc" >&2; fi' EXIT
[ "$(id -u)" = 0 ] || fail 'root required in the disposable guest'
expected=${1:-}
case "$expected" in x86/64|armsr/armv8) ;; *) fail 'usage: verify-fresh.sh x86/64|armsr/armv8';; esac
for tool in uci ubus jsonfilter apk nginx pidof tr awk grep readlink uname; do
    command -v "$tool" >/dev/null || fail "missing required tool: $tool"
done
expect() { [ "$(uci -q get "$1")" = "$2" ] || fail "unexpected UCI value: $1"; }
absent() { [ ! -e "$1" ] && [ ! -L "$1" ] || fail "unexpected path: $1"; }
running() {
    # Do not print process arguments, which may contain private configuration.
    process_pids=$(pidof "$1") || fail "process not running: $1"
    for process_pid in $process_pids; do
        if [ -r "/proc/$process_pid/stat" ] && kill -0 "$process_pid" 2>/dev/null &&
            awk '$3 != "Z" { ok=1 } END { exit !ok }' "/proc/$process_pid/stat"; then
            return 0
        fi
    done
    fail "no live process: $1"
}
mounted() {
    # Require an actual rw/ro mount option, not a substring of another option.
    awk -v point="$1" -v type="$2" -v access="$3" '
        $2 == point && $3 == type {
            count=split($4, flags, ",")
            for (i=1; i<=count; i++) if (flags[i] == access) ok=1
        }
        END { exit !ok }
    ' /proc/mounts
}

phase=identity
. /etc/openwrt_release
[ "$DISTRIB_ID/$DISTRIB_RELEASE/$DISTRIB_TARGET" = "BoltWrt/4.0.0/$expected" ] || fail 'unexpected release or target'
[ "$(uname -r)" = 6.12.112 ] || fail 'unexpected running kernel'
[ "$(cat /proc/1/comm)" = procd ] || fail 'PID1 is not procd'
[ "$(readlink /proc/1/exe)" = /sbin/procd ] || fail 'PID1 executable is not target procd'
case "$expected/$(uname -m)" in x86/64/x86_64|armsr/armv8/aarch64) ;; *) fail 'unexpected running architecture';; esac
boot_id=$(cat /proc/sys/kernel/random/boot_id)
printf '%s\n' "$boot_id" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' || fail 'invalid boot ID'
printf 'IDENTITY target=%s release=4.0.0 kernel=6.12.112 pid1=procd boot_id=%s\n' "$expected" "$boot_id"

phase=mounts
case "$expected" in
    x86/64)
        mounted /rom squashfs ro || fail 'x86 ROM is not mounted squashfs read-only'
        mounted / overlay rw || fail 'x86 root is not writable overlay'
        # Both persistent rootfs_data formats are supported; tmpfs is rejected.
        if ! mounted /overlay ext4 rw && ! mounted /overlay f2fs rw; then
            fail 'x86 overlay is not persistent ext4/f2fs'
        fi
        [ -f /rom/etc/uci-defaults/09-boltwrt-haproxy ] || fail '09 hook absent from image ROM'
        ;;
    armsr/armv8)
        mounted / ext4 rw || fail 'ARM root is not mounted persistent ext4'
        awk '$2 == "/" && $3 == "ext4" && $1 ~ /^\/dev\// { ok=1 } END { exit !ok }' /proc/mounts || fail 'ARM ext4 root is not backed by a block device'
        ;;
esac
awk '$2 == "/" || $2 == "/rom" || $2 == "/overlay" { print "MOUNT " $0 }' /proc/mounts

phase=defaults
[ -f /etc/board.d/99-zz-boltwrt ] && [ ! -L /etc/board.d/99-zz-boltwrt ] || fail 'new board hook absent or replaced'
absent /etc/board.d/99-boltwrt
absent /etc/uci-defaults/09-boltwrt-haproxy
absent /etc/uci-defaults/10_disable_services
expect network.lan interface
expect network.lan.proto static
expect network.lan.device br-lan
expect network.lan.ipaddr 10.126.0.1/18
expect system.@system[0].hostname BoltWrt
[ "$(cat /proc/sys/kernel/hostname)" = BoltWrt ] || fail 'running hostname differs'
expect system.ntp timeserver
expect system.ntp.enabled 1
expect system.ntp.enable_server 0
expect system.ntp.server 'ntp.aliyun.com time.nist.gov ntp.ubuntu.com time.windows.com'
expect luci.main.mediaurlbase /luci-static/argon
running ntpd
ntp_matches=0
for ntp_pid in $process_pids; do
    [ -r "/proc/$ntp_pid/cmdline" ] || continue
    ntp_command=$(tr '\000' ' ' < "/proc/$ntp_pid/cmdline")
    all_peers=1
    for peer in ntp.aliyun.com time.nist.gov ntp.ubuntu.com time.windows.com; do
        case "$ntp_command" in *" -p $peer "*) ;; *) all_peers=0;; esac
    done
    [ "$all_peers" = 0 ] || ntp_matches=1
done
[ "$ntp_matches" = 1 ] || fail 'running ntpd does not have the four default peers'

phase=lan
lan=$(ubus call network.interface.lan status) || fail 'LAN ubus status unavailable'
[ "$(jsonfilter -s "$lan" -e '@.up')" = true ] || fail 'LAN is not up'
[ "$(jsonfilter -s "$lan" -e '@.l3_device')" = br-lan ] || fail 'unexpected LAN layer-3 device'
[ "$(jsonfilter -s "$lan" -e '@["ipv4-address"][*].address')" = 10.126.0.1 ] || fail 'actual LAN IPv4 address differs'
[ "$(jsonfilter -s "$lan" -e '@["ipv4-address"][*].mask')" = 18 ] || fail 'actual LAN prefix differs'
echo 'LAN up=true device=br-lan ipv4=10.126.0.1/18'

phase=web
conf=/etc/nginx/uci.conf
[ -f "$conf" ] && [ -s /var/run/nginx.pid ] || fail 'generated nginx config or master pidfile absent'
nginx_pid=$(cat /var/run/nginx.pid)
case "$nginx_pid" in ''|*[!0-9]*) fail 'invalid nginx master PID';; esac
[ -r "/proc/$nginx_pid/cmdline" ] && kill -0 "$nginx_pid" 2>/dev/null || fail 'nginx master not running'
nginx_command=$(tr '\000' ' ' < "/proc/$nginx_pid/cmdline")
case "$nginx_command" in "nginx: master process /usr/sbin/nginx -c $conf -g "*) ;; *) fail 'nginx master does not use real uci.conf';; esac
nginx -t -c "$conf" >/dev/null 2>&1 || fail 'actual nginx configuration test failed'
running uwsgi
if command -v uhttpd >/dev/null 2>&1 || pidof uhttpd >/dev/null 2>&1; then fail 'unexpected uhttpd executable/process'; fi
echo 'WEB nginx_uci_master=running uwsgi=running uhttpd=absent'

phase=haproxy
[ -x /etc/init.d/haproxy ] || fail 'haproxy init script absent'
if /etc/init.d/haproxy enabled; then fail 'fresh standalone haproxy is enabled'; fi
for link in /etc/rc.d/S??haproxy /etc/rc.d/K??haproxy; do absent "$link"; done
if pidof haproxy >/dev/null 2>&1; then fail 'haproxy unexpectedly running in a fresh guest'; fi
echo 'HAPROXY standalone=disabled process=absent'

phase=packages
absent /usr/lib/opkg/status
absent /usr/lib/opkg/info
if command -v opkg >/dev/null 2>&1; then fail 'unexpected opkg executable'; fi
[ -s /lib/apk/db/installed ] || fail 'real APK installed database absent'
apk_version=$(apk --version) || fail 'APK executable failed'
case "$apk_version" in 'apk-tools 3.0.5,'*) ;; *) fail 'unexpected APK version';; esac
# These are installed-package queries, with no repository update or network call.
for package in base-files boltwrt-defaults luci-base luci-theme-argon \
    nginx-ssl nginx-mod-luci nginx-mod-ubus nginx-ssl-util uwsgi uwsgi-cgi-plugin \
    uwsgi-luci-support uwsgi-syslog-plugin dnsmasq-full smartdns haproxy \
    openvpn-openssl ocserv frpc wireguard-tools sqm-scripts miniupnpd-nftables \
    sing-box xray-core shadowsocks-rust-sslocal shadowsocks-libev-ss-local \
    shadowsocks-libev-ss-redir shadowsocksr-libev-ssr-local \
    shadowsocksr-libev-ssr-redir simple-obfs-client v2ray-plugin; do
    apk info -e "$package" >/dev/null 2>&1 || fail "installed APK query failed: $package"
done
apk info -L boltwrt-defaults | grep -Fx 'etc/uci-defaults/09-boltwrt-haproxy' >/dev/null || fail 'installed defaults payload does not declare the consumed 09 hook'
for backend in dnsmasq smartdns haproxy openvpn ocserv frpc wg miniupnpd \
    sing-box xray sslocal ss-local ss-redir ssr-local ssr-redir obfs-local v2ray-plugin; do
    backend_path=$(command -v "$backend") || fail "backend absent: $backend"
    [ -x "$backend_path" ] || fail "backend not executable: $backend"
done
openvpn --version >/dev/null 2>&1 || fail 'OpenVPN cannot execute its local version query'
printf 'APK %s\n' "$apk_version"
echo 'FRESH_LOCAL_CHECKS_PASSED'
echo 'WEB_TLS_HANDSHAKE_AND_REAL_LUCI_OPERATIONS=PENDING_EXTERNAL_ROOT_CHECK'
echo 'Repeat after a real guest reboot; local checks alone do not prove persistence or product/RAX board acceptance.'
