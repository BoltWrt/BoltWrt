#!/bin/sh
# Real loopback peers, shipped generators and init script, in a disposable guest.
set -eu
umask 077
[ "$(id -u)" = 0 ]
[ -f /root/boltwrt-upgrade-fixture/legacy-400/passwall2_server ]
. /etc/openwrt_release
[ "$DISTRIB_RELEASE" = 4.0.1 ]
directory=/tmp/boltwrt-401-generated
[ -s "$directory/client-xray-tls.json" ]
temporary=$(mktemp -d /tmp/boltwrt-401-proxy.XXXXXX)
cp /etc/config/passwall2_server "$temporary/original-server"
client_pid=
probe_pid=
cleanup() {
    result=$?
    trap - EXIT
    if [ -n "$client_pid" ]; then kill "$client_pid" 2>/dev/null || :; wait "$client_pid" 2>/dev/null || :; fi
    /etc/init.d/passwall2_server stop > "$temporary/stop.log" 2>&1 || result=1
    cp "$temporary/original-server" /etc/config/passwall2_server
    cmp -s "$temporary/original-server" /etc/config/passwall2_server || result=1
    if [ -n "$probe_pid" ]; then kill "$probe_pid" 2>/dev/null || :; wait "$probe_pid" 2>/dev/null || :; fi
    echo "PROXY_EVIDENCE=$temporary"
    exit "$result"
}
trap cleanup EXIT

# Global/per-server enable changes are confined to this test and restored byte
# for byte. All selected listeners bind loopback and add no WAN firewall rule.
lua - <<'LUA'
local c=require("uci").cursor()
local selected={bw401_vless=true,bw401_vless_tls=true,bw401_sbss=true,bw401_native=true}
c:foreach("passwall2_server","global",function(s) assert(c:set("passwall2_server",s[".name"],"enable","1")) end)
c:foreach("passwall2_server","server",function(s) assert(c:set("passwall2_server",s[".name"],"enable",selected[s[".name"]] and "1" or "0")) end)
assert(c:commit("passwall2_server"))
LUA
/etc/init.d/passwall2_server start > "$temporary/start.log" 2>&1

url=http://127.0.0.1:19443/
expected=boltwrt-upgrade-fixture
if ! pidof haproxy >/dev/null; then
    haproxy -db -f /etc/haproxy.cfg > "$temporary/probe.log" 2>&1 &
    probe_pid=$!
fi
for attempt in 1 2 3 4 5; do
    if [ "$(curl --noproxy '*' -sf --max-time 3 "$url" 2>/dev/null || :)" = "$expected" ]; then break; fi
    sleep 1
done
[ "$(curl --noproxy '*' -sf --max-time 3 "$url")" = "$expected" ]

proxy() {
    name=$1; port=$2; shift 2
    "$@" > "$temporary/$name.log" 2>&1 &
    client_pid=$!
    success=0
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$client_pid" 2>/dev/null || break
        if body=$(curl --noproxy '' -sf --max-time 5 --socks5-hostname "127.0.0.1:$port" "$url" 2> "$temporary/$name.curl.log"); then
            [ "$body" = "$expected" ]
            success=1
            break
        fi
        sleep 1
    done
    [ "$success" = 1 ] || { echo "PROXY_FAILED=$name" >&2; return 1; }
    kill "$client_pid"; wait "$client_pid" 2>/dev/null || :; client_pid=
    echo "PROXY_PASSED=$name"
}
proxy xray-vless 19080 /usr/bin/xray run -c "$directory/client-xray.json"
proxy singbox-shadowsocks 19081 /usr/bin/sing-box run -c "$directory/client-singbox.json"
proxy native-hysteria2 19082 /usr/bin/hysteria -c "$directory/client-hysteria.json" client
proxy xray-migrated-certificate-pin 19083 /usr/bin/xray run -c "$directory/client-xray-tls.json"

# The negative control differs only in the certificate pin and local SOCKS port.
lua - <<'LUA'
local json=require("luci.jsonc")
local path="/tmp/boltwrt-401-generated/"
local f=assert(io.open(path .. "client-xray-tls.json")); local c=assert(json.parse(f:read("*a"))); f:close()
local changed=0
for _,outbound in ipairs(c.outbounds) do
    if outbound.protocol=="vless" then outbound.streamSettings.tlsSettings.pinnedPeerCertSha256=string.rep("00",32); changed=changed+1 end
end
assert(changed==1)
for _,inbound in ipairs(c.inbounds) do if inbound.protocol=="socks" then inbound.port=19084 end end
f=assert(io.open(path .. "client-wrong-pin.json","w")); f:write(json.stringify(c)); f:close()
LUA
/usr/bin/xray run -test -c "$directory/client-wrong-pin.json" > "$temporary/wrong-pin-syntax.log" 2>&1
/usr/bin/xray run -c "$directory/client-wrong-pin.json" > "$temporary/wrong-pin.log" 2>&1 &
client_pid=$!
sleep 2
kill -0 "$client_pid"
if curl --noproxy '' -sf --max-time 8 --socks5-hostname 127.0.0.1:19084 "$url" > "$temporary/wrong-pin.body" 2> "$temporary/wrong-pin.curl.log"; then
    echo 'WRONG_CERTIFICATE_PIN_ACCEPTED' >&2
    exit 1
fi
kill -0 "$client_pid"
grep -iE 'pinned|certificate|fingerprint|certsha' "$temporary/wrong-pin.log" >/dev/null
echo 'WRONG_CERTIFICATE_PIN_REJECTED'
