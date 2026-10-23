"""Recovery guard tests using structural text fixtures, never executable targets."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('boltwrt_recovery', REPO / 'scripts/boltwrt-recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
PACKAGES = REPO / 'config/recovery/cmcc-rax3000m.txt'
TARGET = REPO / 'config/targets/cmcc-rax3000m.txt'


class RecoveryGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.config = self.tmp / '.config'
        # Target fragment plus the resolved upstream APK choice: a config fixture.
        self.config.write_text(TARGET.read_text() + '\nCONFIG_USE_APK=y\n')
        self.requests = recovery.package_names(PACKAGES)

    def write_packages(self, names):
        path = self.tmp / 'packages.txt'
        path.write_text('\n'.join(names) + '\n')
        return path

    def make_root(self):
        """Only file/manifest/service structure; no ELF, boot or shell execution."""
        root = self.tmp / 'root-mediatek.recovery'
        root.mkdir()
        files = ('etc/preinit', 'sbin/procd', 'sbin/netifd', 'sbin/uci',
                 'bin/ubus', 'bin/busybox', 'usr/sbin/dropbear', 'sbin/sysupgrade',
                 'lib/upgrade/platform.sh', 'lib/upgrade/nand.sh')
        for name in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('structural fixture only: ' + name + '\n')
        defaults = root / 'etc/board.d/99-boltwrt'
        defaults.parent.mkdir(parents=True)
        defaults.write_bytes((REPO / 'package/boltwrt-defaults/files/etc/board.d/99-boltwrt').read_bytes())
        (root / 'etc/rc.d').mkdir()
        (root / 'etc/init.d').mkdir()
        for service in ('network', 'dropbear', 'dnsmasq', 'firewall'):
            (root / 'etc/init.d' / service).write_text('structural service fixture\n')
            (root / 'etc/rc.d' / ('S50' + service)).symlink_to('../init.d/' + service)
        manifest = self.tmp / 'recovery.manifest'
        manifest.write_text(''.join(name + ' - fixture-version\n' for name in self.requests))
        return SimpleNamespace(root=root, manifest=manifest, config=self.config, packages=PACKAGES)

    def test_current_51_requests_and_rax_config(self):
        self.assertEqual(len(self.requests), 51)
        self.assertEqual(len(set(self.requests)), 51)
        self.assertTrue({'dnsmasq-full', 'nftables-json', 'wpad-basic-mbedtls',
                         'fit-check-sign', 'ubi-utils', 'block-mount'} <= set(self.requests))
        recovery.validate_config(self.config)

    def test_reject_wrong_platform(self):
        self.config.write_text(self.config.read_text().replace(
            'CONFIG_TARGET_mediatek_filogic_DEVICE_cmcc_rax3000m=y',
            'CONFIG_TARGET_x86_64=y'))
        with self.assertRaisesRegex(ValueError, 'DEVICE_cmcc_rax3000m'):
            recovery.validate_config(self.config)

    def test_reject_multi_or_per_device_roots(self):
        baseline = self.config.read_text()
        for option in ('TARGET_MULTI_PROFILE', 'TARGET_PER_DEVICE_ROOTFS'):
            with self.subTest(option=option):
                self.config.write_text(baseline + 'CONFIG_' + option + '=y\n')
                with self.assertRaisesRegex(ValueError, option):
                    recovery.validate_config(self.config)

    def test_reject_missing_core_request(self):
        for name in sorted(recovery.CORE):
            with self.subTest(package=name):
                with self.assertRaisesRegex(ValueError, 'core packages missing'):
                    recovery.package_names(self.write_packages(n for n in self.requests if n != name))

    def test_reject_production_request(self):
        for name in ('nginx-ssl', 'shadowsocks-rust-sslocal', 'luci-base'):
            with self.subTest(package=name):
                with self.assertRaisesRegex(ValueError, 'production services'):
                    recovery.package_names(self.write_packages(self.requests + [name]))

    def test_accept_structural_root_without_executing_it(self):
        args = self.make_root()
        record = recovery.check_root(args, self.requests)
        self.assertEqual(record['package_count'], 51)
        self.assertIn('no target execution or boot validation', record['scope'])

    def test_reject_production_or_symlink_root(self):
        args = self.make_root()
        separate = args.root
        args.root = self.tmp / 'root-mediatek'
        args.root.mkdir()
        with self.assertRaisesRegex(ValueError, 'separate root-mediatek.recovery'):
            recovery.check_root(args, self.requests)
        actual = self.tmp / 'structural-root'
        separate.rename(actual)
        separate.symlink_to(actual, target_is_directory=True)
        args.root = separate
        with self.assertRaisesRegex(ValueError, 'separate root-mediatek.recovery'):
            recovery.check_root(args, self.requests)

    def test_reject_missing_installed_core(self):
        args = self.make_root()
        args.manifest.write_text(args.manifest.read_text().replace('dropbear - fixture-version\n', ''))
        with self.assertRaisesRegex(ValueError, 'packages not installed'):
            recovery.check_root(args, self.requests)

    def test_reject_production_dependency_in_manifest(self):
        args = self.make_root()
        baseline = args.manifest.read_text()
        for name in ('nginx-ssl', 'luci-base'):
            with self.subTest(package=name):
                args.manifest.write_text(baseline + name + ' - fixture-version\n')
                with self.assertRaisesRegex(ValueError, 'unexpected production services'):
                    recovery.check_root(args, self.requests)

    def test_reject_missing_service_rc_entry(self):
        args = self.make_root()
        for service in ('network', 'dropbear', 'dnsmasq', 'firewall'):
            with self.subTest(service=service):
                entry = args.root / 'etc/rc.d' / ('S50' + service)
                entry.unlink()
                with self.assertRaisesRegex(ValueError, 'service not enabled: ' + service):
                    recovery.check_root(args, self.requests)
                entry.symlink_to('../init.d/' + service)

    def test_absolute_and_relative_links_resolve_inside_root(self):
        root = self.tmp / 'link-root'
        (root / 'bin').mkdir(parents=True)
        target = root / 'bin/busybox'
        target.write_text('structural fixture, not host /bin/busybox\n')
        (root / 'absolute').symlink_to('/bin/busybox')
        (root / 'bin/relative').symlink_to('busybox')
        self.assertEqual(recovery.root_file(root, 'absolute'), target)
        self.assertEqual(recovery.root_file(root, 'bin/relative'), target)

    def test_reject_path_and_symlink_escape(self):
        root = self.tmp / 'link-root'
        root.mkdir()
        (self.tmp / 'outside').write_text('must stay outside\n')
        (root / 'escape').symlink_to('../outside')
        for name in ('../outside', 'escape'):
            with self.subTest(path=name):
                with self.assertRaisesRegex(ValueError, 'escapes root'):
                    recovery.root_file(root, name)

    def test_reject_symlink_loop(self):
        root = self.tmp / 'link-root'
        root.mkdir()
        (root / 'a').symlink_to('b')
        (root / 'b').symlink_to('a')
        with self.assertRaisesRegex(ValueError, 'symlink loop'):
            recovery.root_file(root, 'a')


if __name__ == '__main__':
    unittest.main()
