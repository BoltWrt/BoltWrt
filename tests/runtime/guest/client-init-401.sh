#!/bin/sh
# Whole-client init, nftables and DNS integration in a disposable x86 guest.
set -eu
umask 077
[ "$(id -u)" = 0 ] && [ -d /rom ] && [ -d /proc/1 ]
. /etc/openwrt_release
[ "$DISTRIB_RELEASE/$DISTRIB_TARGET" = 4.0.1/x86/64 ]
[ "$(uci -q get passwall2.@global[0].enabled)" = 0 ]
[ "$(uci -q get passwall2_server.@global[0].enable)" = 0 ]
[ "$(uci -q get passwall2.bw401_xray_tls.transport)" = raw ]
[ "$(uci -q get passwall2_server.bw401_vless_tls.bind_local)" = 1 ]
for attempt in $(seq 1 90); do
    [ ! -f /var/lock/passwall2_ready.lock ] || break
    sleep 1
done
[ -f /var/lock/passwall2_ready.lock ]
report=$(mktemp -d /tmp/boltwrt-401-client-init.XXXXXX)
for name in passwall2 passwall2_server dhcp; do cp -p "/etc/config/$name" "$report/$name.original"; done
if [ -f /etc/crontabs/root ]; then cp -p /etc/crontabs/root "$report/root.crontab.original"; fi
probe_pid=
dns_pid=
cleanup() {
    result=$?
    trap - EXIT
    set +e
    [ ! -f /tmp/log/passwall2.log ] || cp /tmp/log/passwall2.log "$report/client-before-stop.log"
    [ ! -d /tmp/etc/passwall2 ] || cp -a /tmp/etc/passwall2 "$report/client-runtime-before-stop"
    /etc/init.d/passwall2 stop > "$report/stop-client.log" 2>&1
    [ "$?" = 0 ] || result=1
    /etc/init.d/passwall2_server stop > "$report/stop-server.log" 2>&1
    [ "$?" = 0 ] || result=1
    for process in "$probe_pid" "$dns_pid"; do
        [ -z "$process" ] || { kill "$process" 2>/dev/null; wait "$process" 2>/dev/null; }
    done
    [ ! -f /tmp/log/passwall2.log ] || cp /tmp/log/passwall2.log "$report/client.log"
    nft list ruleset > "$report/after-stop.nft" 2>&1
    [ ! -d /tmp/etc/passwall2 ] || cp -a /tmp/etc/passwall2 "$report/client-runtime-after-stop"
    for name in passwall2 passwall2_server dhcp; do
        cp -p "$report/$name.original" "/etc/config/$name"
        cmp -s "$report/$name.original" "/etc/config/$name" || result=1
    done
    if [ -f "$report/root.crontab.original" ]; then
        cp -p "$report/root.crontab.original" /etc/crontabs/root
        cmp -s "$report/root.crontab.original" /etc/crontabs/root || result=1
    else
        rm -f /etc/crontabs/root
    fi
    /etc/init.d/dnsmasq restart > "$report/restore-dnsmasq.log" 2>&1
    [ "$?" = 0 ] || result=1
    /etc/init.d/cron restart > "$report/restore-cron.log" 2>&1
    [ "$?" = 0 ] || result=1
    printf '%s\n' "$result" > "$report/exit"
    echo "CLIENT_INIT_EVIDENCE=$report"
    exit "$result"
}
trap cleanup EXIT
nft list ruleset > "$report/before.nft"
if nft -j list table inet passwall2 > "$report/before-client.json" 2>/dev/null; then
    lua - "$report/before-client.json" <<'LUA'
local f=assert(io.open(arg[1]));local rules=assert(require('luci.jsonc').parse(f:read('*a')));f:close()
for _,item in ipairs(rules.nftables) do assert(not item.rule,'unexpected active client rule') end
LUA
fi
lua - <<'LUA'
local c=require('uci').cursor()
c:foreach('passwall2_server','server',function(s)
    assert(c:set('passwall2_server',s['.name'],'enable',s['.name']=='bw401_vless_tls' and '1' or '0'))
end)
c:foreach('passwall2_server','global',function(s) assert(c:set('passwall2_server',s['.name'],'enable','1')) end)
assert(c:commit('passwall2_server'))
-- The migration fixture intentionally omits UI-only labels. The node editor
-- requires remarks; provide a label when using this node as the global client.
assert(c:set('passwall2','bw401_xray_tls','remarks','BoltWrt 401 local init fixture'))
local values={enabled='1',node='bw401_xray_tls',node_socks_port='19088',
    localhost_proxy='1',client_proxy='1',socks_enabled='0',acl_enable='1',
    dns_redirect='0',direct_dns_protocol='udp',direct_dns='127.0.0.1:19553',
    remote_dns_protocol='udp',remote_dns='127.0.0.1:19553',loglevel='debug'}
c:foreach('passwall2','global',function(s)
    for k,v in pairs(values) do assert(c:set('passwall2',s['.name'],k,v)) end
end)
assert(c:commit('passwall2'))
LUA

dnsmasq -k -C /dev/null --no-resolv --no-hosts --bind-interfaces \
    --listen-address=127.0.0.1 --port=19553 --user=root --group=root \
    --log-queries --log-facility=- --address=/fixture.boltwrt.example.com/93.184.215.14 > "$report/upstream-dns.log" 2>&1 &
dns_pid=$!
haproxy -db -f /etc/haproxy.cfg > "$report/probe.log" 2>&1 &
probe_pid=$!
/etc/init.d/passwall2_server start > "$report/start-server.log" 2>&1
/etc/init.d/passwall2 start > "$report/start-client.log" 2>&1
if grep -iE '^Error:|Failed to parse JSON|^lua:|not found' "$report/start-client.log" > "$report/start-errors.log"; then exit 1; fi
nft list table inet passwall2 > "$report/client.nft"
grep -q 'redirect' "$report/client.nft"
if uci -q get passwall2.bw401_acl_global >/dev/null; then
    grep -q '10.126.0.33' "$report/client.nft"
    grep -q '10.126.0.36' "$report/client.nft"
fi
core_pid=$(busybox pgrep -f '/tmp/etc/passwall2/bin/xray' | head -n 1)
[ -n "$core_pid" ] && kill -0 "$core_pid"
core_start=$(awk '{print $22}' "/proc/$core_pid/stat")
printf '%s %s\n' "$core_pid" "$core_start" > "$report/client-process.txt"
cp -a /tmp/etc/passwall2 "$report/client-runtime"
uci export dhcp > "$report/active-dhcp.uci"
ubus call service list '{"name":"dnsmasq"}' > "$report/dnsmasq-service.json"
netstat -lnup > "$report/listeners.txt" 2>&1
set --
for path in /var/etc/dnsmasq* /tmp/dnsmasq*; do [ ! -e "$path" ] || set -- "$@" "$path"; done
if [ "$#" -gt 0 ]; then tar -czf "$report/system-dnsmasq.tar.gz" "$@" 2> "$report/system-dnsmasq-tar.log"; fi

lua - "$report" <<'LUA'
local nixio=require('nixio');local json=require('luci.jsonc');local directory=arg[1]
local f=assert(io.open('/tmp/etc/passwall2/acl/acl_default.json'));local config=assert(json.parse(f:read('*a')));f:close()
local core_port;for _,inbound in ipairs(config.inbounds) do if inbound.tag=='dns-in' then core_port=inbound.port end end
assert(core_port)
local question='\084\069\001\000\000\001\000\000\000\000\000\000\007fixture\007boltwrt\007example\003com\000\000\001\000\001'
local summary={}
for _,peer in ipairs({{'upstream',19553},{'core',core_port}}) do
    local socket=assert(nixio.socket('inet','dgram'));assert(socket:setopt('socket','rcvtimeo',3))
    assert(socket:connect('127.0.0.1',peer[2]));assert(socket:send(question)==#question)
    local response=socket:recv(4096);socket:close()
    summary[peer[1]]={bytes=response and #response or 0,rcode=response and response:byte(4)%16 or -1,
        expected_address=response and response:find('\093\184\215\014',1,true)~=nil or false}
end
f=assert(io.open(directory .. '/dns-peer-probes.json','w'));f:write(json.stringify(summary,1));f:close()
assert(summary.upstream.rcode==0 and summary.upstream.expected_address,'local DNS peer failed')
assert(summary.core.rcode==0 and summary.core.expected_address,'shipped core DNS failed')
print('LOCAL_PEER_AND_CORE_DNS_PASSED')
LUA

flow() {
    local label=$1
    [ "$(curl --noproxy '' -sf --max-time 10 --socks5-hostname 127.0.0.1:19088 http://127.0.0.1:19443/ 2> "$report/$label.curl.log")" = boltwrt-upgrade-fixture ]
    nslookup fixture.boltwrt.example.com 10.126.0.1 > "$report/$label.dns.log" 2>&1
    grep -A 5 'Name:[[:space:]]*fixture\.boltwrt\.example\.com' "$report/$label.dns.log" | grep -q '93\.184\.215\.14'
    echo "CLIENT_INIT_FLOW_PASSED=$label"
}
flow started
/etc/init.d/dnsmasq reload > "$report/dnsmasq-reload.log" 2>&1
sleep 5
flow after-dnsmasq-reload
pidof dnsmasq | tr ' ' '\n' | sort > "$report/dns-pids.before"
sleep 20
[ "$(awk '{print $22}' "/proc/$core_pid/stat")" = "$core_start" ]
pidof dnsmasq | tr ' ' '\n' | sort > "$report/dns-pids.after"
cmp -s "$report/dns-pids.before" "$report/dns-pids.after"
flow stable
echo 'CLIENT_INIT_NFT_DNS_RELOAD_PASSED'
