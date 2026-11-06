"""Stable/RC product identity boundaries; no firmware or target execution."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('boltwrt_verify', Path(__file__).parents[1] / 'boltwrt-verify.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class ProductVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        self.root_patch = patch.object(verify, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def identity(self, locked, configured=None):
        (self.root / 'config/source-lock.json').write_text(json.dumps({'version': locked}))
        (self.root / 'config/version.txt').write_text(
            f'CONFIG_VERSION_NUMBER="{configured if configured is not None else locked}"\n')

    def metadata(self, version, target):
        directory = self.root / 'images'
        directory.mkdir(exist_ok=True)
        if target == 'x86-64':
            platform, device = 'x86-64', 'generic'
            image = f'boltwrt-{version}-{platform}-{device}-squashfs-combined-efi.img.gz'
        else:
            platform, device = 'mediatek-filogic', 'cmcc_rax3000m'
            image = f'boltwrt-{version}-{platform}-{device}-squashfs-sysupgrade.itb'
        manifest = directory / f'boltwrt-{version}-{platform}-{device}.manifest'
        manifest.write_text('kernel - 6.12.112~fixture\n')
        profiles = {'version_number': version,
                    'target': 'x86/64' if target == 'x86-64' else 'mediatek/filogic',
                    'linux_kernel': {'version': '6.12.112'},
                    'profiles': {device: {'image_prefix': f'boltwrt-{version}-{platform}-{device}',
                                          'images': [{'name': image}]}}}
        (directory / 'profiles.json').write_text(json.dumps(profiles))
        return directory, manifest, profiles

    def test_stable_and_rc_identity_and_both_target_names(self):
        for version in ('4.0.0', '4.0.0-rc1'):
            self.identity(version)
            self.assertEqual(verify.product_version(), version)
            for target in ('x86-64', 'cmcc-rax3000m'):
                with self.subTest(version=version, target=target):
                    directory, manifest, profiles = self.metadata(version, target)
                    self.assertEqual(verify.image_metadata(directory, target, version), (manifest, profiles))
                    manifest.unlink()

    def test_lock_fragment_conflict_or_duplicate_is_rejected(self):
        self.identity('4.0.0-rc1', '4.0.0')
        with self.assertRaisesRegex(ValueError, 'fragment/source-lock mismatch'):
            verify.product_version()
        self.identity('4.0.0-rc1')
        fragment = self.root / 'config/version.txt'
        fragment.write_text(fragment.read_text() * 2)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            verify.product_version()

    def test_unsafe_or_noncanonical_version_is_rejected(self):
        for version in ('4.0.0-*', '../4.0.0', '4.0.0-rc0', '4.0.0-rc01', '04.0.0', '4.0.0\n'):
            with self.subTest(version=version):
                self.identity(version)
                with self.assertRaisesRegex(ValueError, 'invalid locked product version'):
                    verify.product_version()

    def test_profile_version_target_and_member_must_match(self):
        directory, _, original = self.metadata('4.0.0-rc1', 'x86-64')
        for field, value in (('version_number', '4.0.0'), ('target', 'mediatek/filogic'),
                             ('image_prefix', 'boltwrt-4.0.0-x86-64-generic'),
                             ('image', 'boltwrt-4.0.0-x86-64-generic-squashfs-combined-efi.img.gz')):
            with self.subTest(field=field):
                profiles = json.loads(json.dumps(original))
                if field == 'image':
                    profiles['profiles']['generic']['images'][0]['name'] = value
                elif field == 'image_prefix':
                    profiles['profiles']['generic']['image_prefix'] = value
                else:
                    profiles[field] = value
                (directory / 'profiles.json').write_text(json.dumps(profiles))
                with self.assertRaisesRegex(ValueError, 'version/target mismatch'):
                    verify.image_metadata(directory, 'x86-64', '4.0.0-rc1')

    def test_rc_manifest_cannot_satisfy_stable_version_or_reverse(self):
        for actual, expected in (('4.0.0-rc1', '4.0.0'), ('4.0.0', '4.0.0-rc1')):
            directory, manifest, _ = self.metadata(actual, 'x86-64')
            with self.assertRaisesRegex(ValueError, 'found 0'):
                verify.image_metadata(directory, 'x86-64', expected)
            manifest.unlink()


if __name__ == '__main__':
    unittest.main()
