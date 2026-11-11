-- Native-UCI fixture for a disposable official 4.0.0 -> 4.0.1 upgrade.
-- Credentials below are public test values. Never run on a configured router.
local mode = assert(arg[1], "expected prepare, verify or generate")
local root = "/root/boltwrt-upgrade-fixture"
local legacy = root .. "/legacy-400"
local uci = require("uci").cursor()
local function read(path)
    local f = assert(io.open(path, "rb")); local value = f:read("*a"); f:close(); return value
end
local function write(path, value)
    local f = assert(io.open(path, "wb")); assert(f:write(value)); f:close()
end
local function put(config, id, kind, options)
    assert(not uci:get(config, id), "fixture section already exists")
    assert(uci:set(config, id, kind))
    for key, value in pairs(options) do assert(uci:set(config, id, key, value)) end
end
local function release(expected)
    assert(read("/etc/openwrt_release"):find("DISTRIB_RELEASE='" .. expected .. "'", 1, true), "unexpected release")
end
local uuid1 = "00000000-0000-4000-8000-000000000001"
local uuid2 = "00000000-0000-4000-8000-000000000002"
local servers = {
    bw401_vless = {type="Xray", protocol="vless", transport="tcp", uuid={uuid1, uuid2}},
    bw401_vless_tls = {type="Xray", protocol="vless", transport="tcp", uuid={uuid1}, tls="1"},
    bw401_xss = {type="Xray", protocol="shadowsocks", method="aes-128-gcm", password="fixture-xss"},
    bw401_socks = {type="Xray", protocol="socks", auth="0", udp_forward="1"},
    bw401_auth = {type="Xray", protocol="socks", auth="1", username="fixture-user", password="fixture-auth"},
    bw401_tunnel = {type="Xray", protocol="dokodemo-door", d_address="127.0.0.1", d_port="80", d_protocol="tcp"},
    bw401_sbss = {type="sing-box", protocol="shadowsocks", method="aes-128-gcm", password="fixture-sbss"},
    bw401_naive = {type="sing-box", protocol="naive", username="fixture-naive", password="fixture-naive-pass", tls="1"},
    bw401_anytls = {type="sing-box", protocol="anytls", password="fixture-anytls", tls="1"},
    bw401_trojan = {type="sing-box", protocol="trojan", uuid={"fixture-trojan-a", "fixture-trojan-b"}, tls="1"},
    bw401_hy = {type="sing-box", protocol="hysteria", hysteria_auth_type="base64", hysteria_auth_password="YWJjAAE=", hysteria_up_mbps="100", hysteria_down_mbps="100", tls="1"},
    bw401_tuic = {type="sing-box", protocol="tuic", uuid=uuid1, password="fixture-tuic", tls="1"},
    bw401_sbhy2 = {type="sing-box", protocol="hysteria2", hysteria2_auth_password="fixture-sbhy2", tls="1"},
    bw401_native = {type="Hysteria2", hysteria2_auth_password="fixture-native"},
    bw401_ss = {type="SS", method="aes-128-gcm", password="fixture-ss"},
    bw401_ssr = {type="SSR", method="aes-128-cfb", password="fixture-ssr", protocol="origin", obfs="plain"},
    bw401_rust = {type="SS-Rust", method="aes-128-gcm", password="fixture-rust"},
}
local ports = {bw401_vless=19100, bw401_sbss=19101, bw401_native=19102, bw401_vless_tls=19105}
local ids = {}; for id in pairs(servers) do ids[#ids+1]=id end; table.sort(ids)
local function same(a,b)
    if type(a) ~= type(b) then return false end
    if type(a) ~= "table" then return a == b end
    if #a ~= #b then return false end
    for i,v in ipairs(a) do if v ~= b[i] then return false end end
    return true
end

if mode == "prepare" then
    release("4.0.0")
    assert(io.open(root .. "/intent"), "prepare the common upgrade fixture first")
    assert(not io.open(legacy .. "/passwall2"), "legacy fixture already exists")
    local existing = 0
    uci:foreach("passwall2_server", "user", function() existing=existing+1 end)
    assert(existing == 0, "source server configuration is not fresh")
    local globals = 0
    uci:foreach("passwall2_server", "global", function(s) assert(uci:set("passwall2_server",s[".name"],"enable","0")); globals=globals+1 end)
    assert(globals == 1)
    for number,id in ipairs(ids) do
        local options = servers[id]
        options.port = tostring(ports[id] or (19110+number))
        options.enable = (number % 2 == 0) and "1" or "0"
        options.bind_local = "1"
        options.remarks = "BoltWrt 4.0.0 upgrade fixture " .. id
        options.accept_lan = "1"
        options.tls_certificateFile = root .. "/test.crt"
        options.tls_keyFile = root .. "/test.key"
        put("passwall2_server", id, "user", options)
    end
    -- Deliberately occupy the first generated account name.
    put("passwall2_server", "bw401_bw401_vless_1", "user", {username="existing-user", password="existing-account-password"})
    put("passwall2", "bw401_xray", "nodes", {type="Xray", protocol="vless", address="127.0.0.1", port="19100", uuid=uuid1, transport="tcp", encryption="none", tls="0", stream_security="none"})
    local certificate=assert(io.popen("openssl x509 -in " .. root .. "/test.crt -outform DER | openssl dgst -sha256"))
    local pin=assert(certificate:read("*a"):match("(%x+)%s*$")); certificate:close(); assert(#pin==64)
    put("passwall2", "bw401_xray_tls", "nodes", {type="Xray", protocol="vless", address="127.0.0.1", port="19105", uuid=uuid1, transport="tcp", encryption="none", tls="1", stream_security="tls", tls_serverName="boltwrt-upgrade-fixture.invalid", tls_CertSha=pin})
    put("passwall2", "bw401_singbox", "nodes", {type="sing-box", protocol="shadowsocks", address="127.0.0.1", port="19101", method="aes-128-gcm", password="fixture-sbss"})
    put("passwall2", "bw401_ws", "nodes", {type="sing-box", protocol="vless", address="127.0.0.1", port="19100", uuid=uuid1, transport="ws", ws_path="/ws?token=a%2Fb", ws_maxEarlyData="2048", ws_earlyDataHeaderName="Sec-WebSocket-Protocol"})
    put("passwall2", "bw401_hysteria", "nodes", {type="Hysteria2", address="127.0.0.1", port="19102", hysteria2_auth_password="fixture-native", tls_serverName="boltwrt-upgrade-fixture.invalid", tls_allowInsecure="1"})
    put("passwall2", "bw401_ss", "nodes", {type="SS", address="127.0.0.1", port="19103", method="aes-128-gcm", password="fixture-ss"})
    put("passwall2", "bw401_ssr", "nodes", {type="SSR", address="127.0.0.1", port="19104", method="aes-128-cfb", password="fixture-ssr", protocol="origin", obfs="plain"})
    uci:foreach("passwall2", "global", function(s) assert(uci:set("passwall2", s[".name"], "enabled", "0")) end)
    for _,config in ipairs({"passwall2","passwall2_server"}) do assert(uci:commit(config)) end
    assert(os.execute("umask 077; mkdir -p " .. legacy .. "/state") == 0)
    for _,config in ipairs({"passwall2","passwall2_server"}) do
        write(legacy .. "/" .. config, read("/etc/config/" .. config))
        local backup = assert(io.open("/etc/sysupgrade.conf", "a"))
        backup:write(legacy .. "/" .. config .. "\n"); backup:close()
    end
    print("PASS seeded official 4.0.0 native UCI fixture: " .. #ids .. " servers, five client types, a TLS certificate pin and an existing account collision")
elseif mode == "verify" then
    release("4.0.1")
    local old = require("uci").cursor(legacy, legacy .. "/state")
    for _,config in ipairs({"passwall2","passwall2_server"}) do
        assert(read("/etc/boltwrt/migrations/4.0.1/" .. config) == read(legacy .. "/" .. config), "private original config backup differs")
        old:foreach(config, nil, function(section)
            local id = section[".name"]
            local migrated = config == "passwall2_server" and servers[id] ~= nil
            assert(uci:get(config,id) == (migrated and "server" or section[".type"]), "section identity/schema differs: " .. id)
            for key,value in pairs(section) do
                if key:sub(1,1) ~= "." then
                    local expected=value
                    if key == "transport" and section.type == "Xray" and value == "tcp" then expected="raw" end
                    if migrated and key == "protocol" and section.type == "Xray" and value == "dokodemo-door" then expected="tunnel" end
                    if config == "passwall2" and id == "bw401_ws" and key == "ws_path" then expected="/ws?token=a%2Fb&ed=2048&eh=Sec-WebSocket-Protocol" end
                    assert(same(uci:get(config,id,key), expected), "preserved option differs: " .. id .. "." .. key)
                end
            end
        end)
    end
    for _,id in ipairs(ids) do
        assert(uci:get("passwall2_server",id,"firewall_allow") == "0", "loopback server firewall intent changed")
        local spec = servers[id]
        if spec.protocol == "shadowsocks" then
            assert(uci:get("passwall2_server",id,"ss_method") == spec.method)
            assert(uci:get("passwall2_server",id,"ss_password") == spec.password)
        elseif spec.protocol == "dokodemo-door" then
            assert(uci:get("passwall2_server",id,"protocol") == "tunnel")
        end
    end
    local users = uci:get("passwall2_server","bw401_vless","users")
    assert(type(users)=="table" and #users==2 and users[1]=="bw401_bw401_vless_2")
    assert(uci:get("passwall2_server",users[1],"uuid")==uuid1 and uci:get("passwall2_server",users[2],"uuid")==uuid2)
    assert(uci:get("passwall2_server","bw401_bw401_vless_1","password")=="existing-account-password")
    assert(uci:get("passwall2","bw401_xray_tls","tls_pinSHA256")==old:get("passwall2","bw401_xray_tls","tls_CertSha"), "Xray certificate pin was not migrated")
    assert(uci:get("passwall2","bw401_singbox","ss_method")=="aes-128-gcm", "client cipher was not migrated")
    for _,case in ipairs({{"bw401_auth","fixture-auth"},{"bw401_naive","fixture-naive-pass"},{"bw401_anytls","fixture-anytls"},{"bw401_hy","YWJjAAE="},{"bw401_tuic","fixture-tuic"},{"bw401_sbhy2","fixture-sbhy2"}}) do
        local accounts=uci:get("passwall2_server",case[1],"users")
        assert(type(accounts)=="table" and #accounts==1, "missing migrated account: " .. case[1])
        assert(uci:get("passwall2_server",accounts[1],"password")==case[2], "migrated authentication differs")
    end
    assert(not uci:get("passwall2_server","bw401_socks","users"), "anonymous SOCKS unexpectedly requires an account")
    for _,id in ipairs({"bw401_ss","bw401_ssr","bw401_rust"}) do
        local account=uci:get("passwall2_server",id,"user")
        assert(account and uci:get("passwall2_server",account,"password")==servers[id].password)
    end
    local before={read("/etc/config/passwall2"),read("/etc/config/passwall2_server")}
    assert(os.execute("lua /usr/share/passwall2/migrate_401.lua apply >/tmp/boltwrt-401-idempotence.log 2>&1")==0)
    assert(before[1]==read("/etc/config/passwall2") and before[2]==read("/etc/config/passwall2_server"), "second migration changed bytes")
    print("PASS actual upgraded UCI: original options, identities, intent, credentials, collision, private backups and idempotence")
elseif mode == "generate" then
    release("4.0.1")
    local json = require("luci.jsonc")
    local api = require("luci.passwall2.api")
    local digest_bytes=""; local digest_hex=""
    for byte=0,31 do digest_bytes=digest_bytes .. string.char(byte); digest_hex=digest_hex .. string.format("%02X",byte) end
    local encoded=require("nixio").bin.b64encode(digest_bytes)
    assert(api.sha256_sb_xray(encoded)==digest_hex, "native NUL-containing certificate digest conversion failed")
    assert(api.sha256_xray_sb(digest_hex)==encoded, "native certificate digest roundtrip failed")
    local websocket=require("luci.passwall2.util_sing-box").gen_outbound("bw401ws",api.uci_get_c("bw401_ws"))
    assert(websocket.transport.path=="/ws?token=a%2Fb" and websocket.transport.max_early_data==2048)
    assert(websocket.transport.early_data_header_name=="Sec-WebSocket-Protocol")
    local directory="/tmp/boltwrt-401-generated"
    assert(os.execute("umask 077; mkdir -p " .. directory)==0)
    local names={Xray="util_xray",["sing-box"]="util_sing-box",Hysteria2="util_hysteria2",SS="util_shadowsocks",SSR="util_shadowsocks",["SS-Rust"]="util_shadowsocks"}
    local inventory={}
    for _,id in ipairs(ids) do
        local node=api.uci_get_s(id)
        local config=require("luci.passwall2." .. names[node.type]).gen_config_server(node)
        local inbound=config.inbounds and config.inbounds[1]
        if inbound then assert(inbound.listen=="127.0.0.1", "loopback bind ignored") end
        if id=="bw401_hy" then assert(inbound.users[1].auth=="YWJjAAE=" and inbound.users[1].auth_str==nil, "binary auth became encoded text") end
        local path=directory .. "/" .. id .. ".json"
        write(path,json.stringify(config,1))
        inventory[#inventory+1]={id=id,type=node.type,path=path}
    end
    local options={flag="bw401",node="bw401_xray",no_run=true,local_socks_address="127.0.0.1",local_socks_port=19080,dns_listen_port=15353,direct_dns_udp_server="127.0.0.1",direct_dns_udp_port=53,direct_dns_query_strategy="UseIP",remote_dns_tcp_server="127.0.0.1",remote_dns_tcp_port=53,remote_dns_query_strategy="UseIP",remote_dns_detour="remote",dns_cache="1"}
    write(directory .. "/client-xray.json",require("luci.passwall2.util_xray").gen_config(options))
    options.node="bw401_xray_tls"; options.flag="bw401tls"; options.local_socks_port=19083
    write(directory .. "/client-xray-tls.json",require("luci.passwall2.util_xray").gen_config(options))
    options.node="bw401_singbox"; options.flag="bw401sb"; options.local_socks_port=19081
    write(directory .. "/client-singbox.json",require("luci.passwall2.util_sing-box").gen_config(options))
    options.node="bw401_hysteria"; options.local_socks_port=19082
    write(directory .. "/client-hysteria.json",require("luci.passwall2.util_hysteria2").gen_config(options))
    write(directory .. "/inventory.json",json.stringify(inventory,1))
    print("PASS actual shipped generators: " .. #ids .. " upgraded servers and Xray/Sing-Box/native Hysteria2 clients, including a migrated TLS pin")
else
    error("unexpected fixture mode")
end
