#!/usr/bin/env python3
"""Compose product choices and reject choices silently lost by defconfig."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ("x86-64", "cmcc-rax3000m")
PASSWALL_CLIENT_BACKENDS = {
    "Haproxy": ("haproxy",),
    "Shadowsocks_Libev_Client": ("shadowsocks-libev-ss-local", "shadowsocks-libev-ss-redir"),
    "Shadowsocks_Rust_Client": ("shadowsocks-rust-sslocal",),
    "ShadowsocksR_Libev_Client": ("shadowsocksr-libev-ssr-local", "shadowsocksr-libev-ssr-redir"),
    "Simple_Obfs": ("simple-obfs-client",),
    "SingBox": ("sing-box",),
    "V2ray_Plugin": ("v2ray-plugin",),
}


def read_config(path):
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if match := re.fullmatch(r"(CONFIG_[A-Za-z0-9_.+-]+)=(.+)", line):
            key, value = match.groups()
        elif match := re.fullmatch(r"# (CONFIG_[A-Za-z0-9_.+-]+) is not set", line):
            key, value = match[1], "n"
        elif not line.strip() or line.startswith("#"):
            continue
        else:
            raise ValueError(f"{path}:{number}: invalid configuration line")
        if key in values and values[key] != value:
            raise ValueError(f"{path}:{number}: conflicting {key}")
        values[key] = value
    return values


def requested(target):
    result = {}
    origins = {}
    for path in (ROOT / "config/version.txt", ROOT / "config/common.txt",
                 ROOT / f"config/targets/{target}.txt"):
        for key, value in read_config(path).items():
            if key in result and result[key] != value:
                raise ValueError(f"{key} conflicts between {origins[key]} and {path}")
            result[key] = value
            origins[key] = path
    return result


def verify(target):
    want = requested(target)
    actual = read_config(ROOT / ".config")
    required = {
        "CONFIG_USE_APK": "y",
        "CONFIG_LINUX_6_12": "y",
        "CONFIG_GCC_VERSION": '"14.3.0"',
        "CONFIG_BINUTILS_VERSION": '"2.44"',
        "CONFIG_USE_MUSL": "y",
        "CONFIG_PACKAGE_opkg": "n",
    }
    if target == "cmcc-rax3000m":
        required["CONFIG_WIFI_SCRIPTS_UCODE"] = "y"
    for key, value in required.items():
        if key in want and want[key] != value:
            raise ValueError(f"product config contradicts the 25.12 baseline: {key}")
        want[key] = value
    errors = [f"{key}: requested {value}, got {actual.get(key, 'n')}"
              for key, value in want.items() if actual.get(key, "n") != value]
    # Package/config selects cannot register a missing official feed provider.
    # Verify the actual backends even if an explicit product choice is removed.
    for option, backends in PASSWALL_CLIENT_BACKENDS.items():
        key = f"CONFIG_PACKAGE_luci-app-passwall2_INCLUDE_{option}"
        if actual.get(key) == "y":
            for backend in backends:
                if actual.get(f"CONFIG_PACKAGE_{backend}") != "y":
                    errors.append(f"{key}: missing selected backend CONFIG_PACKAGE_{backend}=y")
    if not any(actual.get(f"CONFIG_PACKAGE_apk-{tls}") == "y"
               for tls in ("mbedtls", "openssl")):
        errors.append("no APK backend selected")
    if actual.get("CONFIG_PACKAGE_uhttpd") == "y":
        errors.append("uhttpd selected alongside the product nginx frontend")
    if errors:
        raise ValueError("defconfig did not retain product requirements:\n  " + "\n  ".join(errors))
    record = {
        "target": target,
        "config_sha256": hashlib.sha256((ROOT / ".config").read_bytes()).hexdigest(),
        "requested": want,
        "packages": sorted(key.removeprefix("CONFIG_PACKAGE_") for key, value in actual.items()
                           if key.startswith("CONFIG_PACKAGE_") and value == "y"),
        "toolchain": {key: actual.get(key) for key in required},
    }
    log = ROOT / "logs/boltwrt"
    log.mkdir(parents=True, exist_ok=True)
    (log / f"{target}-config.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"{target}: required choices retained; {len(record['packages'])} built-in packages")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if not args.check_only:
        values = requested(args.target)
        text = "# Generated from config/version.txt, common.txt and the target fragment.\n"
        text += "\n".join(f"# {key} is not set" if value == "n" else f"{key}={value}"
                          for key, value in values.items()) + "\n"
        temporary = ROOT / ".config.boltwrt-tmp"
        temporary.write_text(text)
        temporary.replace(ROOT / ".config")
        result = subprocess.run(["make", "defconfig"], cwd=ROOT, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(result.stdout, end="", flush=True)
        result.check_returncode()
        # The upstream make recipe can hide conf errors behind a successful status.
        if re.search(r"(?m)^(?:[^\n]*:[0-9]+:error:|.*recursive dependency detected!)", result.stdout):
            raise ValueError("Kconfig reported an error despite make's successful exit status")
    verify(args.target)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"boltwrt-config: {error}", file=sys.stderr)
        sys.exit(1)
