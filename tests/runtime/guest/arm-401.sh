#!/bin/sh
# Actual RAX rootfs userland in a disposable ARM64 Docker container.
# This does not boot a RAX kernel, load modules or write device storage.
set -eu
export LC_ALL=C
umask 077
[ -f /.dockerenv ] && [ "$(id -u)" = 0 ] && [ "$(uname -m)" = aarch64 ]
. /etc/openwrt_release
export BOLTWRT_TEST_RELEASE=${BOLTWRT_TEST_RELEASE:-4.0.1}
[ "$DISTRIB_ID/$DISTRIB_RELEASE/$DISTRIB_TARGET" = "BoltWrt/$BOLTWRT_TEST_RELEASE/mediatek/filogic" ]
fixture=${1:?provide the private original 4.0.0 ARM fixture archive}
fixture_sha256=${2:?provide the verified fixture SHA256}
signed_apk=${3:?provide the matching signed delivery translation APK}
tools=${4:-/testtools}
[ -r "$fixture" ] && [ -r "$signed_apk" ] && [ -f "$tools/passwall-401.lua" ]
[ "$(sha256sum "$fixture" | awk '{print $1}')" = "$fixture_sha256" ]
[ ! -e /root/boltwrt-upgrade-fixture ]
[ ! -e /etc/boltwrt/migrations/4.0.1/passwall2 ]
report=/tmp/boltwrt-arm-401-evidence
mkdir "$report"
ubus_pid=
rpcd_pid=
cleanup() {
    result=$?
    trap - EXIT
    for process in "$rpcd_pid" "$ubus_pid"; do
        [ -z "$process" ] || { kill "$process" 2>/dev/null || :; wait "$process" 2>/dev/null || :; }
    done
    printf '%s\n' "$result" > "$report/exit"
    set -- "$report"
    for path in /tmp/boltwrt-401-generated /tmp/boltwrt-401-proxy.* /tmp/boltwrt-401-apk.* /tmp/boltwrt-401-acl.* /tmp/boltwrt-401-idempotence.log /etc/boltwrt/migrations/4.0.1; do
        [ ! -e "$path" ] || set -- "$@" "$path"
    done
    tar -czf /tmp/boltwrt-arm-401-evidence.tar.gz "$@" 2>/tmp/boltwrt-arm-401-tar.log || result=1
    echo 'ARM_RUNTIME_EVIDENCE=/tmp/boltwrt-arm-401-evidence.tar.gz'
    exit "$result"
}
trap cleanup EXIT
printf '%s\n' 'Actual RAX rootfs, ARM64 native userland, original 4.0.0 UCI fixture; no board boot, kernel-module loading or device writes.' > "$report/scope.txt"
printf '%s\n' "$fixture_sha256" > "$report/fixture.sha256"
sha256sum "$0" "$tools/passwall-401.lua" "$tools/proxy-401.sh" "$tools/apk-trust-401.sh" > "$report/test-tools.sha256"
mkdir -p /tmp/run/ubus /tmp/.uci /tmp/state /tmp/log /tmp/etc
chmod 700 /tmp/.uci
tar -xzf "$fixture" -C /

apk --no-network --repositories-file /dev/null query --from installed --installed \
    --format json --fields name,version,depends '*' > "$report/packages.json"
find /lib/modules -type f -name '*.ko' | LC_ALL=C sort > "$report/modules.list"
lua - "$report" <<'LUA'
local directory=assert(arg[1]); local json=require('luci.jsonc')
local function read(path) local f=assert(io.open(path,'rb'));local s=f:read('*a');f:close();return s end
local packages=assert(json.parse(read(directory .. '/packages.json')))
local indexed={}
for _,p in ipairs(packages) do assert(not indexed[p.name]); indexed[p.name]=p end
local expected=os.getenv('BOLTWRT_TEST_RELEASE')=='4.1.0' and '26.11.11-r4' or '26.11.11-r3'
assert(indexed['luci-app-passwall2'].version==expected,'wrong downstream PassWall2 release')
local kernel=assert(indexed.kernel).version
assert(kernel:match('^6%.12%.112~%x+%-r%d+$'),'unexpected packaged kernel ABI')
local kmods=0
for _,p in ipairs(packages) do
    if p.name:match('^kmod%-') then
        local matches=0
        for _,dependency in ipairs(p.depends or {}) do if dependency=='kernel=' .. kernel then matches=matches+1 end end
        assert(matches==1,'kernel ABI dependency differs: ' .. p.name)
        kmods=kmods+1
    end
end
local modules=0
for path in read(directory .. '/modules.list'):gmatch('[^\n]+') do
    assert(path:find('/lib/modules/6.12.112/',1,true)==1,'wrong module release directory')
    local data=read(path)
    assert(data:sub(1,4)=='\127ELF' and data:byte(5)==2 and data:byte(6)==1,'module is not ELF64 little endian')
    assert(data:byte(19)+256*data:byte(20)==183,'module is not AArch64')
    assert((data:match('vermagic=([^%z]+)') or ''):match('^6%.12%.112 '),'wrong module vermagic')
    modules=modules+1
end
assert(kmods>0 and modules>0)
local f=assert(io.open(directory .. '/kernel-abi.json','w'))
f:write(json.stringify({kernel=kernel,kmod_packages=kmods,elf_modules=modules,
    scope='Installed APK ABI dependencies and module ELF/vermagic; no module loading'},1));f:close()
print('ARM_KERNEL_METADATA_PASSED')
LUA

/sbin/ubusd -s /var/run/ubus/ubus.sock > "$report/ubusd.log" 2>&1 &
ubus_pid=$!
for attempt in 1 2 3 4 5 6 7 8 9 10; do [ ! -S /var/run/ubus/ubus.sock ] || break; sleep 1; done
[ -S /var/run/ubus/ubus.sock ] && kill -0 "$ubus_pid"
/sbin/rpcd -s /var/run/ubus/ubus.sock > "$report/rpcd.log" 2>&1 &
rpcd_pid=$!
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if ubus -t 1 list 2>/dev/null | grep -qx uci; then break; fi
    sleep 1
done
ubus -t 3 list > "$report/ubus-services.txt"
grep -qx uci "$report/ubus-services.txt"
kill -0 "$rpcd_pid"

sh /etc/uci-defaults/zz-passwall2-boltwrt-401 > "$report/migrate.log" 2>&1
lua "$tools/passwall-401.lua" verify > "$report/migration-verify.log" 2>&1
lua "$tools/passwall-401.lua" acl > "$report/acl-policy.log" 2>&1
if [ "$BOLTWRT_TEST_RELEASE" = 4.0.1 ]; then
    for name in passwall2 passwall2_server; do
        [ "$(ls -ldn "/etc/boltwrt/migrations/4.0.1/$name" | awk '{print $1, $3, $4}')" = '-rw------- 0 0' ]
    done
    for path in /etc/boltwrt /etc/boltwrt/migrations /etc/boltwrt/migrations/4.0.1; do
        [ "$(ls -ldn "$path" | awk '{print $1, $3, $4}')" = 'drwx------ 0 0' ]
    done
else
    [ ! -e /etc/boltwrt/migrations/4.0.1 ]
    [ ! -e /etc/uci-defaults/94-boltwrt-migration-cleanup ]
fi
ubus -t 3 call uci get '{"config":"passwall2"}' > "$report/rpcd-uci.json"
[ "$(jsonfilter -i "$report/rpcd-uci.json" -e '@.values.bw401_xray.transport')" = raw ]
echo 'ARM_NATIVE_MIGRATION_AND_RPC_PASSED'
lua "$tools/passwall-401.lua" generate > "$report/generate.log" 2>&1

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
sh "$tools/proxy-401.sh" > "$report/proxy.log" 2>&1
sh "$tools/apk-trust-401.sh" "$signed_apk" > "$report/apk.log" 2>&1
echo 'ARM_401_USERLAND_PASSED'
