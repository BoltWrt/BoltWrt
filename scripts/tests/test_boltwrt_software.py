import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("software", Path(__file__).parents[1] / "boltwrt-software.py")
software = importlib.util.module_from_spec(spec)
spec.loader.exec_module(software)


class SoftwareTests(unittest.TestCase):
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
