#!/bin/sh
# Run only inside an isolated, freshly booted official 3.1.2 x86 guest.
# This creates a fixture and a preflight backup; it never flashes or reboots.
set -eu
export LC_ALL=C
metadata() { ls -ldn "$1" | awk '{print $1, $3, $4}'; }
umask 077
fail() { echo "FIXTURE_PREPARE_FAILED: $*" >&2; exit 1; }
trap 'rc=$?; [ "$rc" -eq 0 ] || echo "FIXTURE_PREPARE_INCOMPLETE: inspect this guest; do not reuse it as a completed fixture" >&2' EXIT
[ "$(id -u)" = 0 ] || fail 'root required in the disposable guest'
intent=${1:-}
case "$intent" in enabled|disabled) ;; *) fail 'usage: prepare-312-fixture.sh enabled|disabled';; esac
. /etc/openwrt_release
[ "$DISTRIB_ID/$DISTRIB_RELEASE/$DISTRIB_TARGET" = 'BoltWrt/3.1.2/x86/64' ] || fail 'requires official BoltWrt 3.1.2 x86/64'
for tool in uci openssl sha256sum ls awk sysupgrade opkg tar haproxy cmp mktemp sed grep; do
    command -v "$tool" >/dev/null || fail "missing $tool"
done
root=/root/boltwrt-upgrade-fixture
negative=/usr/local/boltwrt-upgrade-negative-control
[ ! -e "$root" ] && [ ! -L "$root" ] || fail 'fixture directory already exists'
[ ! -e "$negative" ] && [ ! -L "$negative" ] || fail 'negative control already exists'
[ -f /usr/lib/opkg/status ] || fail 'old opkg database absent'
[ -x /etc/init.d/haproxy ] || fail 'old haproxy init absent'
[ -f /rom/etc/haproxy.cfg ] && cmp -s /etc/haproxy.cfg /rom/etc/haproxy.cfg || fail 'haproxy config is not the untouched image default'
for package in network system ddns sqm nginx; do
    [ -z "$(uci changes "$package")" ] || fail "pending UCI changes in $package"
done
[ "$(uci get network.lan)" = interface ] || fail 'LAN section missing'
[ "$(uci get network.lan.ipaddr)" = 10.126.0.1 ] || fail 'unexpected management address'
[ "$(uci get system.@system[0])" = system ] || fail 'system section absent'
[ "$(uci get system.ntp)" = timeserver ] || fail 'named NTP section absent'
[ "$(uci get nginx._lan)" = server ] || fail 'nginx LAN server absent'
case "$(uci get nginx.global.uci_enable)" in 1|true) ;; *) fail 'nginx UCI configuration is disabled';; esac
[ "$(uci get nginx._lan.uci_manage_ssl)" = self-signed ] || fail 'unexpected certificate-management mode'
for section in ddns.boltwrt_upgrade_fixture sqm.boltwrt_upgrade_fixture; do
    if uci -q get "$section" >/dev/null; then fail "existing $section"; fi
done
[ -f /etc/sysupgrade.conf ] && [ ! -L /etc/sysupgrade.conf ] || fail 'unexpected sysupgrade.conf'
[ -z "$(sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' /etc/sysupgrade.conf)" ] || fail 'review existing custom backup entries first'

mkdir -m 700 "$root"
printf '%s\n' "$intent" > "$root/intent"
cat > "$root/probe.sh" <<'EOF'
#!/bin/sh
printf '%s\n' 'boltwrt-upgrade-fixture-safe-probe'
EOF
chmod 750 "$root/probe.sh"
openssl req -new -newkey rsa:2048 -x509 -nodes -days 7 \
    -subj /CN=boltwrt-upgrade-fixture.invalid \
    -keyout "$root/test.key" -out "$root/test.crt" > "$root/openssl.log" 2>&1 || fail 'temporary certificate generation failed'
chmod 600 "$root/test.key"
chmod 644 "$root/test.crt"
openssl x509 -in "$root/test.crt" -outform DER -out "$root/certificate.der" || fail 'temporary certificate parsing failed'
sha256sum "$root/certificate.der" | awk '{print $1}' > "$root/certificate-der.sha256"
rm "$root/certificate.der"
mkdir -p /usr/local
printf '%s\n' 'this-unregistered-file-must-not-survive-sysupgrade' > "$negative"

# Restrict the independent service to loopback; enable/disable does not start it.
cat > /etc/haproxy.cfg <<'EOF'
global
    maxconn 16
defaults
    mode http
    timeout connect 1s
    timeout client 5s
    timeout server 5s
frontend boltwrt_upgrade_fixture
    bind 127.0.0.1:19443
    http-request return status 200 content-type text/plain string boltwrt-upgrade-fixture
EOF
chmod 644 /etc/haproxy.cfg
haproxy -c -q -f /etc/haproxy.cfg > "$root/haproxy-check.log" 2>&1 || fail 'fixture haproxy configuration is invalid'
uci set network.lan.proto=static
uci set network.lan.ipaddr=10.126.0.1
uci set network.lan.netmask=255.255.255.0
uci set system.@system[0].hostname="boltwrt-upgrade-$intent"
uci set system.ntp.enabled=0
uci -q delete system.ntp.server || :
uci add_list system.ntp.server=time.fixture.invalid
uci set ddns.boltwrt_upgrade_fixture=service
uci set ddns.boltwrt_upgrade_fixture.enabled=0
uci set ddns.boltwrt_upgrade_fixture.domain=fixture.example.invalid
uci set ddns.boltwrt_upgrade_fixture.lookup_host=fixture.example.invalid
uci set ddns.boltwrt_upgrade_fixture.service_name=cloudflare.com-v4
uci set sqm.boltwrt_upgrade_fixture=queue
uci set sqm.boltwrt_upgrade_fixture.enabled=0
uci set sqm.boltwrt_upgrade_fixture.interface=br-lan
uci set sqm.boltwrt_upgrade_fixture.download=12345
uci set sqm.boltwrt_upgrade_fixture.upload=2345
uci set sqm.boltwrt_upgrade_fixture.qdisc=cake
uci set sqm.boltwrt_upgrade_fixture.script=piece_of_cake.qos
uci set sqm.boltwrt_upgrade_fixture.use_mq=1
uci set nginx._lan.ssl_certificate="$root/test.crt"
uci set nginx._lan.ssl_certificate_key="$root/test.key"
uci delete nginx._lan.uci_manage_ssl
for package in network system ddns sqm nginx; do uci commit "$package"; done
case "$intent" in enabled) /etc/init.d/haproxy enable;; disabled) /etc/init.d/haproxy disable;; esac
if /etc/init.d/haproxy enabled; then actual=enabled; else actual=disabled; fi
[ "$actual" = "$intent" ] || fail 'haproxy intent could not be set'

sha256sum "$root/probe.sh" "$root/test.crt" "$root/test.key" "$root/intent" \
    "$root/certificate-der.sha256" /etc/haproxy.cfg > "$root/sha256sums"
for path in "$root/probe.sh" "$root/test.crt" "$root/test.key" "$root/intent" \
    "$root/certificate-der.sha256" "$root/sha256sums" /etc/haproxy.cfg; do
    printf '%s %s\n' "$(metadata "$path")" "$path"
done > "$root/metadata"
printf '%s\n' "$root/probe.sh" "$root/test.crt" "$root/test.key" "$root/intent" \
    "$root/certificate-der.sha256" "$root/sha256sums" "$root/metadata" /etc/haproxy.cfg >> /etc/sysupgrade.conf

# Check both the nominal file list and the actual standard backup (including
# the automatically generated service-state marker). Never execute that marker.
tmp=$(mktemp -d /tmp/boltwrt-fixture-preflight.XXXXXX)
sysupgrade -l > "$tmp/list.raw"
sed 's,^/,,' "$tmp/list.raw" > "$tmp/list"
sysupgrade -b "$tmp/backup.tar.gz" > "$tmp/backup.log" 2>&1 || fail 'standard backup creation failed'
tar -tzf "$tmp/backup.tar.gz" > "$tmp/members"
while IFS= read -r path; do
    grep -Fx "${path#/}" "$tmp/list" >/dev/null || fail "missing backup list member: $path"
    grep -Fx "${path#/}" "$tmp/members" >/dev/null || fail "missing archive member: $path"
done <<EOF
$root/probe.sh
$root/test.crt
$root/test.key
$root/intent
$root/certificate-der.sha256
$root/sha256sums
$root/metadata
/etc/haproxy.cfg
EOF
if grep -Fx "${negative#/}" "$tmp/members" >/dev/null; then fail 'negative control unexpectedly included'; fi
while IFS= read -r member; do
    case "$member" in
        /*|../*|*/../*|*/..|usr/lib/opkg/*|lib/apk/*|usr/bin/opkg|bin/opkg|sbin/opkg|sbin/apk|usr/bin/apk|usr/sbin/haproxy|lib/modules/*|overlay/*)
            fail 'backup contains an unsafe path or old package state/payload';;
    esac
done < "$tmp/members"
grep -Fx etc/uci-defaults/10_disable_services "$tmp/members" >/dev/null || fail 'standard service-state marker absent'
tar -xzOf "$tmp/backup.tar.gz" etc/uci-defaults/10_disable_services > "$tmp/service-marker"
if grep -Fx '/etc/init.d/haproxy disable' "$tmp/service-marker" >/dev/null; then marker=disabled; else marker=enabled; fi
[ "$marker" = "$intent" ] || fail 'backup marker does not preserve selected haproxy intent'
echo "FIXTURE_PREPARED: intent=$intent management=10.126.0.1/24 preflight=$tmp"
echo 'No service restart, network reload, firmware flash or reboot was performed.'
echo 'Retain the complete preflight archive privately off-guest before the real standard sysupgrade.'
