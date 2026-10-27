#!/usr/bin/env python3
"""Validate the RAX recovery package set and its separate build-time root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
CORE = {
    "base-files", "boltwrt-defaults", "busybox", "dropbear", "netifd", "procd-ujail",
    "uci", "ubus", "fstools", "fitblk", "fit-check-sign", "ubi-utils", "uboot-envtools",
}
PRODUCTION = {
    "nginx-ssl", "uwsgi", "smartdns", "sing-box", "shadowsocks-rust-sslocal",
    "frpc", "openvpn-openssl", "ocserv", "wireguard-tools", "samba4-server",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_config(path: Path) -> None:
    values = dict(line.split("=", 1) for line in path.read_text().splitlines()
                  if line.startswith("CONFIG_") and "=" in line)
    for key in ("TARGET_mediatek_filogic_DEVICE_cmcc_rax3000m", "USE_APK",
                "TARGET_ROOTFS_INITRAMFS", "TARGET_ROOTFS_INITRAMFS_SEPARATE"):
        require(values.get("CONFIG_" + key) == "y", f"RAX recovery requires CONFIG_{key}=y")
    for key in ("TARGET_MULTI_PROFILE", "TARGET_PER_DEVICE_ROOTFS"):
        require(values.get("CONFIG_" + key) != "y", f"RAX recovery does not support CONFIG_{key}=y")


def package_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text().splitlines():
        name = line.partition("#")[0].strip()
        if not name:
            continue
        require(re.fullmatch(r"[a-z0-9][a-z0-9+_.-]*", name) is not None,
                f"invalid recovery package: {name!r}")
        require(name not in names, f"duplicate recovery package: {name}")
        names.append(name)
    require(CORE <= set(names), f"recovery core packages missing: {sorted(CORE - set(names))}")
    require(not production_packages(names), "production services must not enter the recovery package set")
    return names


def production_packages(names) -> list[str]:
    return sorted(name for name in names if name in PRODUCTION or name.startswith("luci-"))


def root_file(root: Path, relative: str) -> Path:
    """Resolve target-root symlinks without interpreting absolute links on the host."""
    pending = list(PurePosixPath(relative).parts)
    current = root
    links = 0
    while pending:
        part = pending.pop(0)
        if part in ("", ".", "/"):
            continue
        if part == "..":
            require(current != root, f"recovery path escapes root: {relative}")
            current = current.parent
            continue
        current /= part
        if current.is_symlink():
            links += 1
            require(links <= 40, f"recovery symlink loop: {relative}")
            target = PurePosixPath(os.readlink(current))
            current = root if target.is_absolute() else current.parent
            pending = list(target.parts) + pending
    require(stat.S_ISREG(current.stat().st_mode), f"missing recovery file: {relative}")
    return current


def check_root(args, requests: list[str]) -> dict:
    root = args.root.absolute()
    require(root.name == "root-mediatek.recovery" and not root.is_symlink(),
            "expected the separate root-mediatek.recovery directory")
    require(root.is_dir(), "recovery root missing; run package/install first")
    packages = {}
    for line in args.manifest.read_text().splitlines():
        name, separator, version = line.partition(" - ")
        require(bool(separator and version and name not in packages), "invalid recovery manifest")
        packages[name] = version
    require(set(requests) <= packages.keys(),
            f"recovery packages not installed: {sorted(set(requests) - packages.keys())}")
    require(not production_packages(packages),
            f"unexpected production services in recovery: {production_packages(packages)}")
    files = ("etc/preinit", "sbin/procd", "sbin/netifd", "sbin/uci", "bin/ubus",
             "bin/busybox", "usr/sbin/dropbear", "sbin/sysupgrade", "lib/upgrade/platform.sh",
             "lib/upgrade/nand.sh", "etc/board.d/99-zz-boltwrt")
    checked = {name: root_file(root, name) for name in files}
    defaults = ROOT / "package/boltwrt-defaults/files/etc/board.d/99-zz-boltwrt"
    require(digest(checked["etc/board.d/99-zz-boltwrt"]) == digest(defaults),
            "recovery LAN/defaults differ from the production defaults package")
    for service in ("network", "dropbear", "dnsmasq", "firewall"):
        entries = list((root / "etc/rc.d").glob("S[0-9][0-9]" + service))
        require(len(entries) == 1, f"recovery service not enabled: {service}")
        root_file(root, entries[0].relative_to(root).as_posix())
    return {
        "scope": "build-time recovery root; no target execution or boot validation",
        "config_sha256": digest(args.config), "requests_sha256": digest(args.packages),
        "manifest_sha256": digest(args.manifest), "package_count": len(packages),
        "packages": packages, "checked_files": {name: digest(path) for name, path in checked.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("requests", "check-root"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    validate_config(args.config)
    requests = package_names(args.packages)
    if args.command == "requests":
        args.output.write_text("\n".join(requests) + "\n")
    else:
        require(args.root is not None and args.manifest is not None, "check-root needs --root and --manifest")
        record = check_root(args, requests)
        args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"boltwrt-recovery: {error}", file=sys.stderr)
        sys.exit(1)
