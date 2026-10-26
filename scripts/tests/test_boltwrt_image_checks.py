"""Native ptgen disk structures and manifest counterexamples; no boot/target execution."""
import gzip
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

spec = importlib.util.spec_from_file_location('boltwrt_verify', Path(__file__).parents[1] / 'boltwrt-verify.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class DiskChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ptgen = os.environ.get('BOLTWRT_PTGEN') or shutil.which('ptgen')
        if not self.ptgen:
            self.skipTest('native ptgen required for actual upstream disk fixtures')

    def disk(self, efi=True, root_mib=384):
        path = self.root / 'disk.img'
        argv = [self.ptgen, '-o', str(path), '-h', '16', '-s', '63',
                '-t', '83', '-p', '32m', '-t', '83', '-p', str(root_mib) + 'm', '-l', '256']
        if efi:
            argv += ['-g', '-G', 'b001b001-1111-2222-3333-000000000000']
        run = subprocess.run(argv, check=True, capture_output=True, text=True)
        boot_offset, boot_bytes, root_offset, root_bytes = map(int, run.stdout.split())
        self.assertEqual(boot_bytes, 32 * 1024**2)
        self.assertEqual(root_bytes, root_mib * 1024**2)
        # Deliberately only structural SquashFS fields, not a bootable filesystem.
        data = bytearray(96)
        data[:4] = b'hsqs'
        struct.pack_into('<H', data, 28, 4)
        struct.pack_into('<Q', data, 40, 4096)
        with path.open('r+b') as f:
            # gen_image_generic.sh PADDING=1 appends one 63-sector track for
            # GPT; ptgen itself emits the primary metadata, not this full disk.
            f.truncate(max(path.stat().st_size, root_offset + root_bytes + (63 * 512 if efi else 0)))
            f.seek(root_offset)
            f.write(data)
        return path

    def header(self, path, change):
        with path.open('r+b') as f:
            f.seek(512)
            header = bytearray(f.read(512))
            change(header)
            struct.pack_into('<I', header, 16, 0)
            size = struct.unpack_from('<I', header, 12)[0]
            struct.pack_into('<I', header, 16, zlib.crc32(header[:size]))
            f.seek(512)
            f.write(header)

    def table(self, path, change):
        with path.open('r+b') as f:
            f.seek(512)
            header = f.read(512)
            lba, count, size = struct.unpack_from('<QII', header, 72)
            f.seek(lba * 512)
            data = bytearray(f.read(count * size))
            change(data, size)
            f.seek(lba * 512)
            f.write(data)
        self.header(path, lambda h: struct.pack_into('<I', h, 88, zlib.crc32(data)))

    def test_native_ptgen_full_gpt_and_gzip_accepted(self):
        path = self.disk()
        result = verify.check_disk(path, True, compressed=False)
        self.assertEqual([p[1] for p in result['partition_sectors']], [32 * 2048, 384 * 2048])
        packed = self.root / 'disk.img.gz'
        with path.open('rb') as source, gzip.open(packed, 'wb', compresslevel=1) as dest:
            shutil.copyfileobj(source, dest)
        self.assertEqual(verify.check_disk(packed, True), result)

    def test_native_ptgen_bios_accepted(self):
        self.assertEqual(verify.check_disk(self.disk(False), False, compressed=False)['squashfs_bytes'], 4096)

    def test_native_vmdk_roundtrip_preserves_layout(self):
        qemu = shutil.which('qemu-img')
        if not qemu:
            self.skipTest('native qemu-img required')
        path = self.disk()
        vmdk, raw = self.root / 'disk.vmdk', self.root / 'roundtrip.img'
        subprocess.run([qemu, 'convert', '-f', 'raw', '-O', 'vmdk', str(path), str(vmdk)], check=True)
        subprocess.run([qemu, 'convert', '-f', 'vmdk', '-O', 'raw', str(vmdk), str(raw)], check=True)
        self.assertEqual(verify.check_disk(raw, True, compressed=False)['squashfs_bytes'], 4096)

    def test_bad_primary_header_crc_rejected(self):
        path = self.disk()
        with path.open('r+b') as f:
            f.seek(528)
            value = f.read(1)[0]
            f.seek(528)
            f.write(bytes([value ^ 1]))
        with self.assertRaisesRegex(ValueError, 'header CRC'):
            verify.check_disk(path, True, compressed=False)

    def test_bad_partition_table_crc_rejected(self):
        path = self.disk()
        with path.open('r+b') as f:
            f.seek(1024)
            value = f.read(1)[0]
            f.seek(1024)
            f.write(bytes([value ^ 1]))
        with self.assertRaisesRegex(ValueError, 'table CRC'):
            verify.check_disk(path, True, compressed=False)

    def test_partition_outside_usable_with_valid_crcs_rejected(self):
        path = self.disk()
        self.header(path, lambda h: struct.pack_into('<Q', h, 48, 1000))
        with self.assertRaisesRegex(ValueError, 'usable range'):
            verify.check_disk(path, True, compressed=False)

    def test_unused_first_entry_with_valid_crcs_rejected(self):
        path = self.disk()
        self.table(path, lambda t, size: t.__setitem__(slice(0, 16), bytes(16)))
        with self.assertRaisesRegex(ValueError, 'missing GPT boot/root'):
            verify.check_disk(path, True, compressed=False)

    def test_duplicate_partition_guid_with_valid_crcs_rejected(self):
        path = self.disk()
        self.table(path, lambda t, size: t.__setitem__(slice(size + 16, size + 32), t[16:32]))
        with self.assertRaisesRegex(ValueError, 'partition GUID'):
            verify.check_disk(path, True, compressed=False)

    def test_extra_used_partition_cannot_overlap_boot(self):
        path = self.disk()
        def overlap(table, size):
            # Add a third used entry; the first two data partitions stay unchanged.
            table[2 * size:3 * size] = table[:size]
            table[2 * size + 16:2 * size + 32] = bytes([7]) * 16
        self.table(path, overlap)
        with self.assertRaisesRegex(ValueError, 'overlapping GPT'):
            verify.check_disk(path, True, compressed=False)

    def test_unbounded_table_geometry_rejected(self):
        path = self.disk()
        self.header(path, lambda h: struct.pack_into('<I', h, 80, 0xffffffff))
        with self.assertRaisesRegex(ValueError, 'entry geometry'):
            verify.check_disk(path, True, compressed=False)

    def test_backup_geometry_beyond_full_disk_rejected(self):
        path = self.disk()
        self.header(path, lambda h: struct.pack_into('<Q', h, 32, path.stat().st_size // 512 + 1000))
        with self.assertRaisesRegex(ValueError, 'backup geometry'):
            verify.check_disk(path, True, compressed=False)

    def test_wrong_root_partition_size_rejected(self):
        with self.assertRaisesRegex(ValueError, 'root partition is not 384'):
            verify.check_disk(self.disk(root_mib=383), True, compressed=False)


# Independent reduced manifest fixture: names from the actual selected product
# chains, not generated from the verifier's required-package constants.
BASE_PACKAGES = '''apk-mbedtls boltwrt-defaults dnsmasq-full smartdns luci-app-smartdns
ddns-scripts ddns-scripts-aliyun ddns-scripts-services luci-app-ddns
luci-app-passwall2 haproxy sing-box xray-core geoview v2ray-plugin v2ray-geoip v2ray-geosite
shadowsocks-rust-sslocal shadowsocks-libev-ss-local shadowsocks-libev-ss-redir
shadowsocksr-libev-ssr-local shadowsocksr-libev-ssr-redir simple-obfs-client
sqm-scripts luci-app-sqm kmod-sched-cake kmod-ifb miniupnpd-nftables luci-app-upnp
openvpn-openssl luci-app-openvpn ocserv luci-app-ocserv frpc luci-app-frpc
wireguard-tools kmod-wireguard luci-proto-wireguard firewall4 nftables-json ucode
ucode-mod-fs ucode-mod-ubus ucode-mod-uci ppp ppp-mod-pppoe odhcp6c odhcpd-ipv6only
luci-nginx nginx-ssl nginx-mod-luci nginx-mod-ubus nginx-ssl-util uwsgi
uwsgi-cgi-plugin uwsgi-luci-support uwsgi-syslog-plugin luci-theme-argon'''.split()
X86_ONLY = ('zabbix-agentd-basic', 'ddns-scripts-cloudflare', 'ddns-scripts-godaddy', 'ddns-scripts-huaweicloud')


class ManifestChecks(unittest.TestCase):
    def packages(self, x86=False):
        return dict([(p, 'fixture-version') for p in BASE_PACKAGES + (list(X86_ONLY) if x86 else [])] +
                    [('kernel', '6.12.112~fixture')])

    def test_shared_chain_and_platform_difference(self):
        verify.check_manifest(self.packages(), 'cmcc-rax3000m')
        verify.check_manifest(self.packages(True), 'x86-64')

    def test_missing_chosen_backends_and_web_plugins_rejected(self):
        required = ('haproxy', 'ddns-scripts-services', 'nginx-mod-ubus', 'nginx-ssl-util', 'uwsgi', 'uwsgi-cgi-plugin',
                    'uwsgi-luci-support', 'uwsgi-syslog-plugin', 'xray-core', 'geoview',
                    'v2ray-plugin', 'v2ray-geoip', 'v2ray-geosite', 'shadowsocks-rust-sslocal',
                    'shadowsocks-libev-ss-local', 'shadowsocks-libev-ss-redir',
                    'shadowsocksr-libev-ssr-local', 'shadowsocksr-libev-ssr-redir',
                    'simple-obfs-client', 'ucode', 'ucode-mod-fs', 'ucode-mod-ubus', 'ucode-mod-uci')
        for name in required:
            with self.subTest(package=name):
                packages = self.packages()
                del packages[name]
                with self.assertRaisesRegex(ValueError, name):
                    verify.check_manifest(packages, 'cmcc-rax3000m')

    def test_nftables_alternative_does_not_replace_json_backend(self):
        packages = self.packages()
        del packages['nftables-json']
        packages['nftables-nojson'] = 'fixture-version'
        with self.assertRaisesRegex(ValueError, 'nftables-json'):
            verify.check_manifest(packages, 'cmcc-rax3000m')

    def test_missing_x86_provider_rejected_but_not_required_on_rax(self):
        for name in X86_ONLY:
            with self.subTest(package=name):
                packages = self.packages(True)
                del packages[name]
                with self.assertRaisesRegex(ValueError, name):
                    verify.check_manifest(packages, 'x86-64')
        verify.check_manifest(self.packages(), 'cmcc-rax3000m')


if __name__ == '__main__':
    unittest.main()
