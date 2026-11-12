#!/bin/sh
# Original 4.0.0 validation code in Docker, with a recorded board-name fixture.
# Test mode stops before any upgrade operation; no board kernel is emulated.
set -eu
umask 077
[ -f /.dockerenv ] && [ "$(id -u)" = 0 ] && [ "$(uname -m)" = aarch64 ]
. /etc/openwrt_release
[ "$DISTRIB_ID/$DISTRIB_RELEASE/$DISTRIB_TARGET" = BoltWrt/4.0.0/mediatek/filogic ]
image=${1:?provide the FIT sysupgrade image}
expected=${2:?provide its verified SHA256}
[ -f "$image" ] && [ "$(sha256sum "$image" | awk '{print $1}')" = "$expected" ]
mkdir -p /tmp/sysinfo /tmp/.uci /tmp/log
printf '%s\n' cmcc,rax3000m > /tmp/sysinfo/board_name
if /sbin/sysupgrade -T "$image" > /tmp/rax-validation-positive.log 2>&1; then
    cat /tmp/rax-validation-positive.log
else
    result=$?
    cat /tmp/rax-validation-positive.log
    exit "$result"
fi
[ -s /tmp/sysupgrade.meta ]
printf '%s\n' 'ORIGINAL_400_RAX_SYSUPGRADE_TEST_PASSED' 'METADATA_BEGIN'
cat /tmp/sysupgrade.meta
printf '\n%s\n' 'METADATA_END'

# The input file and validation implementation remain identical. Only the
# synthetic board identity changes, so a bad-device rejection is observable.
printf '%s\n' boltwrt,deliberately-invalid-board > /tmp/sysinfo/board_name
if /sbin/sysupgrade -T "$image" > /tmp/rax-validation-negative.log 2>&1; then
    echo 'INVALID_BOARD_WAS_ACCEPTED' >&2
    exit 1
fi
cat /tmp/rax-validation-negative.log
printf '%s\n' 'WRONG_BOARD_REJECTED' 'SCOPE=original firmware validation in Docker with board-name fixture; no kernel boot or flash'
