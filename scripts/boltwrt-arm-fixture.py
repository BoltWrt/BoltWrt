#!/usr/bin/env python3
"""Compose/check the auxiliary armsr VM fixture; never grant product acceptance."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

OVERRIDES = {"CONFIG_TARGET_ROOTFS_EXT4FS": ("n", "y")}
RAX_IMAGE_OPTIONS = {
    "CONFIG_TARGET_ROOTFS_INITRAMFS", "CONFIG_TARGET_INITRAMFS_COMPRESSION_XZ",
    "CONFIG_TARGET_ROOTFS_INITRAMFS_SEPARATE", "CONFIG_TARGET_ROOTFS_PARTSIZE",
}
RAX_HARDWARE = {
    "CONFIG_TARGET_mediatek", "CONFIG_TARGET_mediatek_filogic",
    "CONFIG_TARGET_mediatek_filogic_DEVICE_cmcc_rax3000m",
    *("CONFIG_PACKAGE_u-boot-mt7981_cmcc_rax3000m-" + item
      for item in ("emmc-ddr3", "emmc-ddr4", "nand-ddr3", "nand-ddr4")),
}
BACKENDS = {
    "Haproxy": ("haproxy",),
    "Shadowsocks_Libev_Client": ("shadowsocks-libev-ss-local", "shadowsocks-libev-ss-redir"),
    "Shadowsocks_Rust_Client": ("shadowsocks-rust-sslocal",),
    "ShadowsocksR_Libev_Client": ("shadowsocksr-libev-ssr-local", "shadowsocksr-libev-ssr-redir"),
    "Simple_Obfs": ("simple-obfs-client",),
    "SingBox": ("sing-box",),
    "V2ray_Plugin": ("v2ray-plugin",),
}
MANIFEST_EXTRA = {"luci-base", "luci-app-samba4", "samba4-server", "wsdd2"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_config(path):
    result = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        match = re.fullmatch(r"(CONFIG_[A-Za-z0-9_.+-]+)=(.+)", line)
        unset = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_.+-]+) is not set", line)
        if match:
            key, value = match.groups()
        elif unset:
            key, value = unset[1], "n"
        elif not line.strip() or line.startswith("#"):
            continue
        else:
            raise ValueError(f"{path}:{number}: invalid configuration line")
        if key in result and result[key] != value:
            raise ValueError(f"{path}:{number}: conflicting {key}")
        result[key] = value
    return result


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refuse symlink destination: {path}")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".arm-fixture-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    os.chmod(temporary, 0o644)
    temporary.replace(path)


def compose(root):
    names = ("config/version.txt", "config/common.txt", "config/testing/armsr-armv8.txt")
    values, overrides = {}, {}
    for name in names:
        for key, value in read_config(root / name).items():
            if key in values and values[key] != value:
                if name != names[-1] or OVERRIDES.get(key) != (values[key], value):
                    raise ValueError(f"unexpected fixture override: {key}")
                overrides[key] = [values[key], value]
            values[key] = value
    if overrides != {key: list(value) for key, value in OVERRIDES.items()}:
        raise ValueError("the explicit common ext4 override changed; review fixture policy")
    rax = read_config(root / "config/targets/cmcc-rax3000m.txt")
    excluded = RAX_HARDWARE | RAX_IMAGE_OPTIONS
    if not excluded <= rax.keys():
        raise ValueError("RAX fragment identity changed; review hardware/image exclusions")
    for key, value in rax.items():
        if key not in excluded and values.get(key, "n") != value:
            raise ValueError(f"applicable RAX choice missing: {key}={value}")
    return values, overrides, names


def check(root, want):
    actual = read_config(root / ".config")
    required = dict(want, CONFIG_ARCH='"aarch64"', CONFIG_CPU_TYPE='"generic"',
                    CONFIG_TARGET_BOARD='"armsr"', CONFIG_TARGET_SUBTARGET='"armv8"',
                    CONFIG_TARGET_ARCH_PACKAGES='"aarch64_generic"', CONFIG_USE_APK="y",
                    CONFIG_LINUX_6_12="y", CONFIG_GCC_VERSION='"14.3.0"',
                    CONFIG_BINUTILS_VERSION='"2.44"', CONFIG_USE_MUSL="y",
                    CONFIG_PACKAGE_opkg="n", CONFIG_PACKAGE_uhttpd="n")
    errors = [f"{key}: expected {value}, resolved {actual.get(key, 'n')}"
              for key, value in required.items() if actual.get(key, "n") != value]
    for key in RAX_HARDWARE:
        if actual.get(key, "n") != "n":
            errors.append(f"RAX hardware selected in auxiliary fixture: {key}")
    for option, packages in BACKENDS.items():
        if actual.get("CONFIG_PACKAGE_luci-app-passwall2_INCLUDE_" + option) == "y":
            for package in packages:
                if actual.get("CONFIG_PACKAGE_" + package) != "y":
                    errors.append(f"missing PassWall2 backend: {package}")
    if not any(actual.get("CONFIG_PACKAGE_apk-" + tls) == "y" for tls in ("mbedtls", "openssl")):
        errors.append("no APK TLS backend selected")
    if errors:
        raise ValueError("fixture requirements lost:\n  " + "\n  ".join(errors))
    return actual


def service_packages(root):
    """Read only the literal policy; never execute the product checker or its CLI."""
    path = root / "scripts/boltwrt-verify.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "SERVICE_PACKAGES"
                           for target in node.targets)]
    if len(assignments) != 1:
        raise ValueError("expected one literal SERVICE_PACKAGES policy")
    groups = ast.literal_eval(assignments[0].value)
    if (not isinstance(groups, dict) or not groups
            or any(not isinstance(group, str) or not isinstance(names, (tuple, list))
                   or not names or any(not isinstance(name, str) or not name for name in names)
                   for group, names in groups.items())):
        raise ValueError("unexpected SERVICE_PACKAGES policy shape")
    return groups


def manifest_evidence(path, groups):
    packages = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        match = re.fullmatch(r"([^\s]+) - ([^\s]+)", line)
        if not match or match[1] in packages:
            raise ValueError(f"{path}:{number}: invalid or duplicate manifest package")
        packages[match[1]] = match[2]
    required = MANIFEST_EXTRA | {name for names in groups.values() for name in names}
    missing = required - packages.keys()
    if missing:
        raise ValueError(f"manifest missing required runtime packages: {sorted(missing)}")
    if "opkg" in packages or "uhttpd" in packages:
        raise ValueError("manifest contains excluded package manager/web server")
    if not any(name in packages for name in ("apk-mbedtls", "apk-openssl")):
        raise ValueError("manifest missing APK TLS backend")
    if not re.fullmatch(r"6\.12\.112(?:[-~+].*)?", packages.get("kernel", "")):
        raise ValueError("manifest kernel is not 6.12.112")
    return {"path": str(path), "sha256": sha(path), "packages": packages,
            "required_service_groups": groups, "extra_required_packages": sorted(MANIFEST_EXTRA),
            "scope": "actual supplied image manifest; no runtime proof"}


def input_hashes(root, names):
    return {name: sha(root / name) for name in (*names, "config/source-lock.json",
            "config/targets/cmcc-rax3000m.txt", "scripts/boltwrt-arm-fixture.py",
            "scripts/boltwrt-verify.py")}


def prepare_history(out):
    """Append-only attempts; a failed/latest incomplete attempt never grants rerun."""
    ledger = out / "prepare-attempts"
    for path in (out.parent.parent, out.parent, out, ledger):
        if path.is_symlink():
            raise ValueError(f"refuse symlink prepare history: {path}")
    if not ledger.exists():
        return ledger, []
    if not ledger.is_dir():
        raise ValueError("prepare history is not a directory")
    attempts = sorted(ledger.iterdir())
    if any(not re.fullmatch(r"[0-9]{8}", path.name) or path.is_symlink()
           or not path.is_dir() for path in attempts):
        raise ValueError("unknown prepare history entry")
    if [path.name for path in attempts] != [f"{n:08d}" for n in range(1, len(attempts) + 1)]:
        raise ValueError("incomplete prepare history sequence")
    return ledger, attempts


def require_recorded_prepare(root, attempts, inputs):
    if not attempts:
        raise ValueError("non-raw .config has no successful fixture prepare record")
    last = attempts[-1]
    for name in ("evidence.json", "resolved.config", "defconfig.exit", "defconfig.log"):
        path = last / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("latest fixture prepare did not complete successfully")
    proof = json.loads((last / "evidence.json").read_text())
    if (proof.get("prepare_status") != "defconfig-success"
            or proof.get("input_sha256") != inputs
            or proof.get("config_sha256") != sha(root / ".config")
            or (last / "resolved.config").read_bytes() != (root / ".config").read_bytes()
            or (last / "defconfig.exit").read_text().strip() != "0"
            or proof.get("defconfig_log_sha256") != sha(last / "defconfig.log")):
        raise ValueError("unknown .config or stale/failed fixture prepare provenance")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--check-only", action="store_true", help="check existing resolved .config")
    parser.add_argument("--manifest", type=Path, help="check and record an actual build manifest")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not (root / "include/toplevel.mk").is_file():
        raise ValueError("--root must name the prepared OpenWrt source root")
    out = root / "logs/boltwrt/armsr-armv8-fixture"
    want, overrides, names = compose(root)
    if args.manifest and not args.check_only:
        raise ValueError("--manifest requires --check-only; do not replace a built configuration")
    inputs = input_hashes(root, names)
    config = root / ".config"
    if config.is_symlink():
        raise ValueError("refuse symlink .config")
    attempt = None
    if not args.check_only:
        text = "# Auxiliary VM fixture, not a supported product target.\n"
        text += "\n".join(f"# {key} is not set" if value == "n" else f"{key}={value}"
                          for key, value in want.items()) + "\n"
        ledger, attempts = prepare_history(out)
        if config.exists() and config.read_bytes() != text.encode():
            require_recorded_prepare(root, attempts, inputs)
            check(root, want)
        # Reject unknown config before creating an attempt or invoking make.
        ledger.mkdir(parents=True, exist_ok=True)
        attempt = ledger / f"{len(attempts) + 1:08d}"
        attempt.mkdir()
        if not config.exists():
            write_atomic(config, text.encode())
        result = subprocess.run(["make", "defconfig"], cwd=root, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        write_atomic(attempt / "defconfig.log", result.stdout.encode())
        write_atomic(attempt / "defconfig.exit", f"{result.returncode}\n".encode())
        print(result.stdout, end="", flush=True)
        result.check_returncode()
        if re.search(r"(?m)^(?:[^\n]*:[0-9]+:error:|.*recursive dependency detected!)", result.stdout):
            raise ValueError("Kconfig reported an error despite make exit 0")
    actual = check(root, want)
    evidence = {
        "fixture": "armsr/armv8 generic QEMU virt", "product_target": False,
        "scope": "resolved auxiliary config; no build, boot, EFI or RAX board acceptance",
        "input_sha256": inputs,
        "explicit_common_overrides": overrides,
        "excluded_rax_hardware": sorted(RAX_HARDWARE),
        "replaced_rax_image_options": sorted(RAX_IMAGE_OPTIONS),
        "config_sha256": sha(root / ".config"), "resolved": actual,
        "builtin_config_choices": sorted(key.removeprefix("CONFIG_PACKAGE_")
                                         for key, value in actual.items()
                                         if key.startswith("CONFIG_PACKAGE_") and value == "y"),
    }
    if args.manifest:
        manifest = args.manifest if args.manifest.is_absolute() else root / args.manifest
        manifest = manifest.resolve(strict=True)
        if not manifest.is_relative_to(root / "bin/targets/armsr/armv8"):
            raise ValueError("manifest must be from this root's bin/targets/armsr/armv8")
        evidence["manifest"] = manifest_evidence(manifest, service_packages(root))
        evidence["scope"] = "resolved auxiliary config and actual manifest; no runtime acceptance"
    if args.check_only:
        # Inspection cannot create/refresh prepare success provenance.
        evidence["mode"] = "read-only-check"
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return
    if input_hashes(root, names) != inputs:
        raise ValueError("fixture inputs changed during defconfig")
    evidence["prepare_status"] = "defconfig-success"
    evidence["defconfig_log_sha256"] = sha(attempt / "defconfig.log")
    write_atomic(attempt / "resolved.config", config.read_bytes())
    # Last commit point. Failed make/check/input drift leaves no success record.
    write_atomic(attempt / "evidence.json", (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode())
    print(f"Auxiliary fixture prepared; evidence: {attempt / 'evidence.json'}")



if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"boltwrt-arm-fixture: {error}", file=sys.stderr)
        sys.exit(1)
