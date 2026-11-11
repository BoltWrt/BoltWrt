"""Delivery selection boundaries; native APK signatures are checked on artifacts."""
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
