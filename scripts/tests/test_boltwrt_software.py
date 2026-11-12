import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("software", Path(__file__).parents[1] / "boltwrt-software.py")
software = importlib.util.module_from_spec(spec)
spec.loader.exec_module(software)


class SoftwareTests(unittest.TestCase):
    def policy_archive(self, root, extra=None):
        entries = {
            'etc/config/luci': b'option mediaurlbase /luci-static/argon',
            'www/luci-static/resources/view/system/system.js': b'language settings',
            'usr/share/ucode/luci/template/themes/argon/header.ut': b'theme header',
            'usr/share/ucode/luci/template/themes/argon/page_scripts.ut': b'menu-argon',
            'www/luci-static/argon/css/cascade.css': b'body{}',
            'usr/libexec/rpcd/luci.argon_wallpaper': b'read-only rendering helper',
        }
        entries.update(extra or {})
        path = root / 'rootfs.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, content in entries.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        return path

    def test_product_payload_retains_renderer_without_theme_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = self.policy_archive(Path(directory))
            self.assertEqual(software.check_product_policy(archive)['fixed_theme'], 'argon')

    def test_stale_backend_or_test_cleanup_in_actual_payload_is_rejected(self):
        for name in ('usr/bin/hysteria', 'etc/uci-defaults/94-boltwrt-migration-cleanup',
                     'etc/boltwrt/migrations/4.0.1/passwall2',
                     'www/luci-static/resources/view/argon-config.js'):
            with self.subTest(path=name), tempfile.TemporaryDirectory() as directory:
                archive = self.policy_archive(Path(directory), {name: b'stale file'})
                with self.assertRaisesRegex(ValueError, 'retired product payload'):
                    software.check_product_policy(archive)

    def test_system_view_with_theme_selector_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = self.policy_archive(Path(directory), {
                'www/luci-static/resources/view/system/system.js': b'form.ListValue, _mediaurlbase'})
            with self.assertRaisesRegex(ValueError, 'theme selector remains'):
                software.check_product_policy(archive)

    def test_manifest_requires_the_fixed_downstream_package_release(self):
        policy = {name: {"version": "1.2.3"} for name in software.VERSION_PACKAGES}
        policy["passwall2"]["release"] = 2
        policy.update(geoip="202611050019", geosite="202611102204")
        entries = [f"{package} - 1.2.3-r1" for names in software.VERSION_PACKAGES.values() for package in names]
        entries += ["v2ray-geoip - 202611050019.1", "v2ray-geosite - 202611102204.1"]
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest"
            manifest.write_text("\n".join(entries))
            with self.assertRaisesRegex(ValueError, "downstream release"):
                software.check_versions(manifest, policy)
            manifest.write_text("\n".join(entries).replace("luci-app-passwall2 - 1.2.3-r1", "luci-app-passwall2 - 1.2.3-r2"))
            self.assertEqual(software.check_versions(manifest, policy)["luci-app-passwall2"], "1.2.3-r2")

    def test_rootfs_alias_must_match_and_cannot_hide_stale_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            canonical = directory / "boltwrt-4.0.1-x86-64-generic-rootfs.tar.gz"
            alias = directory / "boltwrt-4.0.1-x86-64-generic-targz-rootfs.tar.gz"
            canonical.write_bytes(b"actual archive selection fixture")
            alias.write_bytes(canonical.read_bytes())
            self.assertEqual(software.rootfs_archive(directory), canonical)
            alias.write_bytes(b"different cohort")
            with self.assertRaisesRegex(ValueError, "alias differs"):
                software.rootfs_archive(directory)
            alias.write_bytes(canonical.read_bytes())
            (directory / "boltwrt-4.0.0-x86-64-generic-rootfs.tar.gz").write_bytes(canonical.read_bytes())
            with self.assertRaisesRegex(ValueError, "one regular canonical"):
                software.rootfs_archive(directory)

    def test_manifest_rejects_stale_payload_version_and_duplicate_entries(self):
        policy = {name: {"version": "1.2.3"} for name in software.VERSION_PACKAGES}
        policy.update(geoip="202611050019", geosite="202611102204", tuic={"version": "1.8.11"})
        entries = [f"{package} - 1.2.3-r1" for names in software.VERSION_PACKAGES.values() for package in names]
        entries += ["v2ray-geoip - 202611050019.1", "v2ray-geosite - 202611102204.1"]
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest"
            manifest.write_text("\n".join(entries) + "\n")
            self.assertIn("xray-core", software.check_versions(manifest, policy))
            manifest.write_text("\n".join(entries).replace("xray-core - 1.2.3-r1", "xray-core - 1.2.2-r1"))
            with self.assertRaisesRegex(ValueError, "xray-core"):
                software.check_versions(manifest, policy)
            manifest.write_text("\n".join(entries + [entries[0]]))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                software.check_versions(manifest, policy)


if __name__ == "__main__":
    unittest.main()
