#!/bin/bash
set -euo pipefail
[ -f /.dockerenv ] && [ "$(id -u)" = 0 ] || { echo "Run only as root inside a disposable Docker container" >&2; exit 1; }
for source in libubox uci; do
	if [ "$source" = libubox ]; then makefile=/work/package/libs/libubox/Makefile; else makefile=/work/package/system/uci/Makefile; fi
	sha=$(sed -n "s/^PKG_SOURCE_VERSION:=//p" "$makefile")
	[ "$(tar -tzf /sources/$source.tar.gz | sed -n "1p" | cut -d/ -f1)" = "$source-$sha" ] || { echo "Source archive does not match baseline $source SHA" >&2; exit 1; }
done

sed -i 's|http://ports.ubuntu.com/ubuntu-ports|https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports|g' /etc/apt/sources.list.d/ubuntu.sources
apt-get update -qq
apt-get install -y -qq --no-install-recommends libjson-c-dev > /tmp/boltwrt-apt.log
mkdir -p /tmp/src/libubox /tmp/src/uci
tar -xzf /sources/libubox.tar.gz -C /tmp/src/libubox --strip-components=1
tar -xzf /sources/uci.tar.gz -C /tmp/src/uci --strip-components=1
cmake -S /tmp/src/libubox -B /tmp/build-libubox -DCMAKE_INSTALL_PREFIX=/usr -DBUILD_LUA=OFF -DBUILD_EXAMPLES=OFF > /tmp/libubox.log
cmake --build /tmp/build-libubox -j4 >> /tmp/libubox.log
cmake --install /tmp/build-libubox >> /tmp/libubox.log
cmake -S /tmp/src/uci -B /tmp/build-uci -DCMAKE_INSTALL_PREFIX=/usr -DBUILD_LUA=OFF > /tmp/uci.log
cmake --build /tmp/build-uci -j4 >> /tmp/uci.log
cmake --install /tmp/build-uci >> /tmp/uci.log
ldconfig
ln -sf /usr/bin/uci /sbin/uci
mkdir -p /var/state

mkdir -p /etc/config /tmp/.uci /lib/functions
cp /work/package/base-files/files/lib/functions.sh /lib/functions.sh
mkdir -p /lib/config
cp /work/package/system/uci/files/lib/config/uci.sh /lib/config/uci.sh

cp /work/package/base-files/files/lib/functions/{uci-defaults.sh,ipv4.sh} /lib/functions/
python3 /work/package/boltwrt-defaults/tests/test_defaults.py
