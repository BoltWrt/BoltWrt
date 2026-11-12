"""Actual INCLUDE/backend contracts; no defconfig, network or firmware build."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("config", Path(__file__).parents[1] / "boltwrt-config.py")
config = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(Path(spec.origin).parent), *sys.path]):
    spec.loader.exec_module(config)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="boltwrt-config-")
        self.root = Path(self.temp.name)
        (self.root / "config/targets").mkdir(parents=True)
        (self.root / "config/version.txt").write_text("")
        (self.root / "config/targets/x86-64.txt").write_text("")
        self.actual = {
            "CONFIG_USE_APK": "y", "CONFIG_LINUX_6_12": "y",
            "CONFIG_GCC_VERSION": '"14.3.0"', "CONFIG_BINUTILS_VERSION": '"2.44"',
            "CONFIG_USE_MUSL": "y", "CONFIG_PACKAGE_apk-mbedtls": "y",
        }
        self.root_patch = patch.object(config, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def verify(self, options, backends=()):
        # Product fragment deliberately contains INCLUDE only: guards must catch
        # the original failure even if explicit backend choice is lost later.
        fragment = "".join(f"CONFIG_PACKAGE_luci-app-passwall2_{o if o.startswith('Basic_Core_') else 'INCLUDE_' + o}=y\n" for o in options)
        (self.root / "config/common.txt").write_text(fragment)
        text = "".join(f"{k}={v}\n" for k, v in self.actual.items()) + fragment
        text += "".join(f"CONFIG_PACKAGE_{p}=y\n" for p in backends)
        (self.root / ".config").write_text(text)
        config.verify("x86-64")

    def test_include_without_registered_haproxy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing selected backend CONFIG_PACKAGE_haproxy=y"):
            self.verify(("Haproxy",))
        self.assertFalse((self.root / "logs/boltwrt/x86-64-config.json").exists())

    def test_removed_include_is_rejected_even_when_its_binary_is_absent(self):
        with self.assertRaisesRegex(ValueError, "obsolete PassWall2 INCLUDE_Hysteria"):
            self.verify(("Hysteria",), ("sing-box",))

    def test_removed_binary_is_rejected_without_an_include(self):
        for name in config.REMOVED_BACKENDS:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "excludes standalone backend"):
                self.verify((), (name,))

    def test_basic_core_all_requires_both_registered_cores(self):
        with self.assertRaisesRegex(ValueError, "CONFIG_PACKAGE_xray-core=y"):
            self.verify(("Basic_Core_All",), ("sing-box",))

    def test_upstream_client_options_with_actual_backends_pass(self):
        self.verify(("Haproxy", "Shadowsocks_Rust_Client",
                     "ShadowsocksR_Libev_Client", "Simple_Obfs", "Basic_Core_All", "V2ray_Plugin"),
                    ("haproxy", "xray-core",
                     "shadowsocks-rust-sslocal", "shadowsocksr-libev-ssr-local",
                     "shadowsocksr-libev-ssr-redir", "simple-obfs-client", "sing-box", "v2ray-plugin"))
        record = json.loads((self.root / "logs/boltwrt/x86-64-config.json").read_text())
        self.assertIn("haproxy", record["packages"])

    def test_fixed_theme_rejects_a_second_theme(self):
        with self.assertRaisesRegex(ValueError, "fixed-theme policy"):
            self.verify((), ("luci-theme-argon", "luci-theme-bootstrap"))

    def test_disabled_includes_do_not_enable_extra_backends(self):
        self.verify(())
        record = json.loads((self.root / "logs/boltwrt/x86-64-config.json").read_text())
        self.assertNotIn("haproxy", record["packages"])

    def test_release_tag_drives_config_generation_and_check_only(self):
        fragment = self.root / "config/version.txt"
        fragment.write_text('CONFIG_VERSION_NUMBER="4.0.0"\n')
        common = self.root / "config/common.txt"
        common.write_text("".join(f"{key}={value}\n" for key, value in self.actual.items()))

        args = ["boltwrt-config.py", "x86-64", "--release-tag", "v4.1.0-rc2"]
        with patch.object(sys, "argv", args), patch.object(
            config.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "")
        ):
            config.main()
        self.assertEqual(config.read_config(self.root / ".config")["CONFIG_VERSION_NUMBER"], '"4.1.0-rc2"')
        self.assertEqual(fragment.read_text(), 'CONFIG_VERSION_NUMBER="4.0.0"\n')

        with patch.object(sys, "argv", [*args, "--check-only"]):
            config.main()
        with self.assertRaisesRegex(ValueError, "CONFIG_VERSION_NUMBER"):
            config.verify("x86-64")

    def test_external_bootstrap_rejects_old_seed_and_the_legacy_chain(self):
        self.verify(())
        actual = self.root / ".config"
        initial = actual.read_text()
        directory = self.root / "bootstrap"
        directory.mkdir()
        configured = initial + 'CONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT=' + json.dumps(str(directory)) + '\n'
        actual.write_text(configured + '# CONFIG_GOLANG_BUILD_BOOTSTRAP is not set\n')
        with patch.object(config.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, "go version go1.23.12 linux/arm64\n")):
            with self.assertRaisesRegex(ValueError, ">= Go 1.24"):
                config.verify("x86-64")
        with patch.object(config.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, "go version go1.26.10 linux/arm64\n")):
            config.verify("x86-64")
        record = json.loads((self.root / "logs/boltwrt/x86-64-config.json").read_text())
        self.assertIn("go1.26.10", record["go_bootstrap"]["version"])
        actual.write_text(configured + 'CONFIG_GOLANG_BUILD_BOOTSTRAP=y\n')
        with self.assertRaisesRegex(ValueError, "GOLANG_BUILD_BOOTSTRAP disabled"):
            config.verify("x86-64")


if __name__ == "__main__":
    unittest.main()
