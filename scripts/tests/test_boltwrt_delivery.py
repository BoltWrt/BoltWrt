"""Delivery selection boundaries; native APK signatures are checked on artifacts."""
import importlib.util
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("delivery", Path(__file__).parents[1] / "boltwrt-delivery.py")
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_installed_cohort_requires_exact_versions_and_unambiguous_files(self):
        base = self.root / "bin/targets/x86/64/packages"
        base.mkdir(parents=True)
        package = base / "example-1.0-r1.apk"
        package.write_bytes(b"fixture selection only")
        self.assertEqual(delivery.package_files(self.root, "x86-64", {"example": "1.0-r1"}), {package.name: package})
        with self.assertRaisesRegex(ValueError, "no matching build APK"):
            delivery.package_files(self.root, "x86-64", {"example": "2.0-r1"})
        other = self.root / "bin/packages/x86_64/custom"
        other.mkdir(parents=True)
        (other / package.name).write_bytes(b"conflicting build output")
        with self.assertRaisesRegex(ValueError, "ambiguous APK"):
            delivery.package_files(self.root, "x86-64", {"example": "1.0-r1"})

    def test_apk_symlinks_are_not_published(self):
        base = self.root / "bin/targets/x86/64/packages"
        base.mkdir(parents=True)
        secret = self.root / "unrelated"
        secret.write_bytes(b"not an APK")
        (base / "example-1.0-r1.apk").symlink_to(secret)
        with self.assertRaisesRegex(ValueError, "not a regular build artifact"):
            delivery.package_files(self.root, "x86-64", {"example": "1.0-r1"})

    def rootfs(self, name, data):
        path = self.root / "rootfs.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        return path

    def test_only_image_public_keys_are_exported(self):
        public = b"-----BEGIN PUBLIC KEY-----\npublic selection fixture\n-----END PUBLIC KEY-----\n"
        path = self.rootfs("./etc/apk/keys/build.pem", public)
        self.assertEqual(delivery.public_keys(path), {"build.pem": public})
        path = self.rootfs("etc/apk/keys/build.pem", public + b"PRIVATE KEY")
        with self.assertRaisesRegex(ValueError, "invalid/duplicate"):
            delivery.public_keys(path)
        path = self.rootfs("etc/apk/keys/../../escape", public)
        with self.assertRaisesRegex(ValueError, "unexpected APK public key"):
            delivery.public_keys(path)

    def test_incremental_delivery_refreshes_both_checksum_lists(self):
        directory = self.root
        image = directory / 'boltwrt-fixture.img.gz'
        image.write_bytes(b'image')
        provenance = directory / 'boltwrt-fixture-build.json'
        provenance.write_bytes(b'previous provenance')
        metadata = directory / 'config.buildinfo'
        metadata.write_bytes(b'build configuration')
        standard = directory / 'sha256sums'
        standard.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
                                   for p in (image, provenance, metadata)))
        delivery.update_checksums(directory, 'boltwrt-fixture')
        provenance.write_bytes(b'current provenance')
        bundle = directory / 'boltwrt-fixture-installed-packages.tar.gz'
        bundle.write_bytes(b'current signed bundle')
        delivery.update_checksums(directory, 'boltwrt-fixture')
        for checksum in (standard, directory / 'boltwrt-fixture-SHA256SUMS'):
            for line in checksum.read_text().splitlines():
                digest, name = line.split('  ', 1)
                self.assertEqual(digest, hashlib.sha256((directory / name).read_bytes()).hexdigest(), name)
        self.assertIn(bundle.name, standard.read_text())
        self.assertIn('config.buildinfo', standard.read_text())
        self.assertNotIn('sha256sums', standard.read_text())

    def test_rax_standard_checksums_only_reference_published_files(self):
        image = self.root / 'boltwrt-fixture-sysupgrade.itb'
        image.write_bytes(b'RAX fixture image')
        metadata = self.root / 'profiles.json'
        metadata.write_bytes(b'RAX profiles')
        loader = self.root / 'mt7988-ram-ddr4-bl2.bin'
        loader.write_bytes(b'unpublished generic MediaTek loader')
        standard = self.root / 'sha256sums'
        standard.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
                                   for p in (image, metadata, loader)))
        delivery.update_checksums(self.root, 'boltwrt-fixture')
        names = {line.split('  ', 1)[1] for line in standard.read_text().splitlines()}
        self.assertEqual(names, {image.name, metadata.name, 'boltwrt-fixture-SHA256SUMS'})
        self.assertTrue(loader.exists())
        for line in standard.read_text().splitlines():
            digest, name = line.split('  ', 1)
            self.assertEqual(digest, hashlib.sha256((self.root / name).read_bytes()).hexdigest())

    def test_standard_checksum_cannot_read_outside_artifacts_or_create_a_cycle(self):
        standard = self.root / 'sha256sums'
        for name in ('../private-key.pem', 'sha256sums'):
            standard.write_text('0' * 64 + '  ' + name + '\n')
            with self.assertRaisesRegex(ValueError, 'invalid/duplicate'):
                delivery.update_checksums(self.root, 'boltwrt-fixture')
        secret = self.root / 'unrelated'
        secret.write_bytes(b'private fixture')
        link = self.root / 'metadata'
        link.symlink_to(secret)
        standard.write_text('0' * 64 + '  metadata\n')
        with self.assertRaisesRegex(ValueError, 'not a regular build artifact'):
            delivery.update_checksums(self.root, 'boltwrt-fixture')


if __name__ == "__main__":
    unittest.main()
