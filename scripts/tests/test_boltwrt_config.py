"""Actual INCLUDE/backend contracts; no defconfig, network or firmware build."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("config", Path(__file__).parents[1] / "boltwrt-config.py")
config = importlib.util.module_from_spec(spec)
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
        fragment = "".join(f"CONFIG_PACKAGE_luci-app-passwall2_INCLUDE_{o}=y\n" for o in options)
        (self.root / "config/common.txt").write_text(fragment)
        text = "".join(f"{k}={v}\n" for k, v in self.actual.items()) + fragment
        text += "".join(f"CONFIG_PACKAGE_{p}=y\n" for p in backends)
        (self.root / ".config").write_text(text)
        config.verify("x86-64")

    def test_include_without_registered_haproxy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing selected backend CONFIG_PACKAGE_haproxy=y"):
            self.verify(("Haproxy",))
        self.assertFalse((self.root / "logs/boltwrt/x86-64-config.json").exists())

    def test_client_with_only_one_libev_binary_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "CONFIG_PACKAGE_shadowsocks-libev-ss-redir=y"):
            self.verify(("Shadowsocks_Libev_Client",), ("shadowsocks-libev-ss-local",))

    def test_seven_selected_client_options_with_actual_backends_pass(self):
        self.verify(("Haproxy", "Shadowsocks_Libev_Client", "Shadowsocks_Rust_Client",
                     "ShadowsocksR_Libev_Client", "Simple_Obfs", "SingBox", "V2ray_Plugin"),
                    ("haproxy", "shadowsocks-libev-ss-local", "shadowsocks-libev-ss-redir",
                     "shadowsocks-rust-sslocal", "shadowsocksr-libev-ssr-local",
                     "shadowsocksr-libev-ssr-redir", "simple-obfs-client", "sing-box", "v2ray-plugin"))
        record = json.loads((self.root / "logs/boltwrt/x86-64-config.json").read_text())
        self.assertIn("haproxy", record["packages"])

    def test_disabled_includes_do_not_enable_extra_backends(self):
        self.verify(())
        record = json.loads((self.root / "logs/boltwrt/x86-64-config.json").read_text())
        self.assertNotIn("haproxy", record["packages"])


if __name__ == "__main__":
    unittest.main()
