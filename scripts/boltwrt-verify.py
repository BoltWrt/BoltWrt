#!/usr/bin/env python3
"""Check built manifests and image layouts. This does not boot a firmware."""
import argparse
import gzip
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE_PACKAGES = {
    "DNS": ("dnsmasq-full", "smartdns", "luci-app-smartdns"),
    "DDNS": ("ddns-scripts", "ddns-scripts-aliyun", "luci-app-ddns"),
    "proxy": ("luci-app-passwall2", "sing-box"),
    "SQM": ("sqm-scripts", "luci-app-sqm", "kmod-sched-cake", "kmod-ifb"),
    "UPnP": ("miniupnpd-nftables", "luci-app-upnp"),
    "OpenVPN": ("openvpn-openssl", "luci-app-openvpn"),
    "ocserv": ("ocserv", "luci-app-ocserv"),
    "frpc": ("frpc", "luci-app-frpc"),
    "WireGuard": ("wireguard-tools", "kmod-wireguard", "luci-proto-wireguard"),
    "network": ("firewall4", "ppp", "ppp-mod-pppoe", "odhcp6c", "odhcpd-ipv6only"),
    "web": ("luci-nginx", "nginx-ssl", "nginx-mod-luci", "luci-theme-argon"),
    "defaults": ("boltwrt-defaults",),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def check_disk(path, efi, compressed=True):
    opener = gzip.open if compressed else open
    with opener(path, "rb") as image:
        header = image.read(1024)
        require(header[510:512] == b"\x55\xaa", f"{path.name}: missing MBR signature")
        partitions = []
        if efi:
            require(header[512:520] == b"EFI PART", f"{path.name}: missing GPT")
            entry_lba, count, size = struct.unpack_from("<QII", header, 512 + 72)
            require(128 <= size <= 4096 and count >= 2, "invalid GPT entry geometry")
            image.seek(entry_lba * 512)
            entries = image.read(2 * size)
            for offset in (0, size):
                first, last = struct.unpack_from("<QQ", entries, offset + 32)
                require(last >= first > 0, "invalid GPT partition bounds")
                partitions.append((first, last - first + 1))
        else:
            require(header[446] == 0x80, f"{path.name}: BIOS partition is not bootable")
            for offset in (446, 462):
                require(header[offset + 4] != 0, "missing MBR partition")
                partitions.append(struct.unpack_from("<II", header, offset + 8))
        require(partitions[0][1] == 32 * 2048, f"{path.name}: boot partition is not 32 MiB")
        require(partitions[1][1] == 384 * 2048, f"{path.name}: root partition is not 384 MiB")
        require(partitions[1][0] >= sum(partitions[0]), "overlapping disk partitions")
        image.seek(partitions[1][0] * 512)
        squashfs = image.read(96)
        require(squashfs[:4] == b"hsqs", f"{path.name}: root partition is not SquashFS")
        require(struct.unpack_from("<H", squashfs, 28)[0] == 4, "unexpected SquashFS version")
        used = struct.unpack_from("<Q", squashfs, 40)[0]
        require(used <= partitions[1][1] * 512, "SquashFS exceeds root partition")
        # Reading to EOF validates the compressed image CRC without loading it in memory.
        while image.read(1024 * 1024):
            pass
        require(image.tell() >= sum(partitions[1]) * 512, "truncated disk image")
    return {"partition_sectors": partitions, "squashfs_bytes": used}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("x86-64", "cmcc-rax3000m"))
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    subprocess.run([sys.executable, str(ROOT / "scripts/boltwrt-config.py"), args.target,
                    "--check-only"], check=True)
    target = "x86/64" if args.target == "x86-64" else "mediatek/filogic"
    directory = args.directory or ROOT / "bin/targets" / target
    manifests = list(directory.glob("boltwrt-4.0.0-*.manifest"))
    require(len(manifests) == 1, f"expected one current image manifest, found {len(manifests)}")
    packages = {}
    for line in manifests[0].read_text().splitlines():
        name, separator, version = line.partition(" - ")
        require(bool(separator and name and version), f"invalid manifest line: {line}")
        require(name not in packages, f"duplicate manifest package: {name}")
        packages[name] = version
    require("opkg" not in packages and any(p in packages for p in ("apk-mbedtls", "apk-openssl")),
            "manifest does not contain an APK-only package manager")
    require("uhttpd" not in packages, "unexpected second web server in image")
    for service, names in SERVICE_PACKAGES.items():
        missing = set(names) - packages.keys()
        require(not missing, f"{service}: missing actual backend/frontend packages: {sorted(missing)}")
    require(any(name.startswith("nftables") for name in packages), "missing nftables backend")
    require(packages.get("kernel", "").startswith("6.12.112"), "unexpected built kernel")
    # The locked sch_cake module provides both cake and cake_mq. A package
    # manifest cannot establish which qdisc is active; that needs runtime checks.
    profiles = json.loads((directory / "profiles.json").read_text())
    require(profiles["version_number"] == "4.0.0" and profiles["target"] == target,
            "image profile version/target mismatch")
    require(profiles["linux_kernel"]["version"] == "6.12.112", "profile kernel mismatch")
    record = {"target": args.target, "manifest": manifests[0].name,
              "manifest_sha256": digest(manifests[0]), "packages": packages, "images": {}}
    for profile in profiles["profiles"].values():
        for item in profile["images"]:
            path = directory / item["name"]
            require(path.parent == directory and path.is_file(), "missing or invalid profile artifact")
            require(path.stat().st_size == item["size"] and digest(path) == item["sha256"],
                    f"artifact content differs from profile: {path.name}")
            record["images"][path.name] = {"bytes": path.stat().st_size, "sha256": item["sha256"]}
    if args.target == "x86-64":
        require("zabbix-agentd-basic" in packages, "x86 TLS monitoring package lost")
        for suffix, efi in (("generic-squashfs-combined.img.gz", False),
                            ("generic-squashfs-combined-efi.img.gz", True)):
            path = directory / ("boltwrt-4.0.0-x86-64-" + suffix)
            require(path.name in record["images"], f"missing x86 image: {path.name}")
            record["images"][path.name].update(check_disk(path, efi))
        temporary_root = ROOT / "tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="boltwrt-vmdk-", dir=temporary_root) as temporary:
            for suffix, efi in (("combined.vmdk", False), ("combined-efi.vmdk", True)):
                path = directory / ("boltwrt-4.0.0-x86-64-generic-squashfs-" + suffix)
                require(path.is_file() and path.name in record["images"], f"missing VMDK: {path.name}")
                info = json.loads(subprocess.check_output(["qemu-img", "info", "--output=json", str(path)]))
                require(info["format"] == "vmdk" and info["virtual-size"] >= 416 * 1024 * 1024,
                        f"invalid VMDK format/size: {path.name}")
                require("backing-filename" not in info, "VMDK must be self contained")
                raw = Path(temporary) / "disk.img"
                subprocess.run(["qemu-img", "convert", "-f", "vmdk", "-O", "raw", str(path), str(raw)],
                               check=True)
                record["images"][path.name].update(check_disk(raw, efi, compressed=False))
                record["images"][path.name]["qemu_info"] = info
    else:
        rax_packages = {"samba4-server", "wsdd2", "wifi-scripts", "kmod-mt7915e",
                        "kmod-mt7981-firmware", "mt7981-wo-firmware", "fitblk", "block-mount",
                        "kmod-usb3", "kmod-usb-storage-uas", "kmod-fs-ext4", "kmod-fs-exfat",
                        "kmod-fs-ntfs3", "kmod-fs-vfat"}
        missing = rax_packages - packages.keys()
        require(not missing, f"RAX wireless/boot/storage packages missing: {sorted(missing)}")
        require(any(name in packages for name in ("wpad-basic-mbedtls", "wpad-mbedtls",
                                                  "wpad-basic-openssl", "wpad-openssl")),
                "RAX Wi-Fi authenticator missing")
        prefix = "boltwrt-4.0.0-mediatek-filogic-cmcc_rax3000m-"
        image = directory / (prefix + "squashfs-sysupgrade.itb")
        require(image.is_file(), "missing RAX sysupgrade FIT")
        require(0 < image.stat().st_size < 104 * 1024 * 1024,
                "RAX image exceeds conservative 104 MiB NAND budget; not safe for common profile")
        with image.open("rb") as handle:
            require(handle.read(4) == b"\xd0\x0d\xfe\xed", "RAX image is not a FIT")
        for medium in ("emmc", "nand"):
            for ddr in ("ddr3", "ddr4"):
                for kind in ("preloader.bin", "bl31-uboot.fip"):
                    path = directory / f"{prefix}{medium}-{ddr}-{kind}"
                    require(path.is_file() and path.stat().st_size > 0, f"missing hardware artifact: {path.name}")
                    record["images"][path.name] = {"bytes": path.stat().st_size, "sha256": digest(path)}
        require((directory / (prefix + "emmc-gpt.bin")).is_file(), "missing eMMC GPT artifact")
        require(any(directory.glob(prefix + "*-recovery.itb")), "missing RAX initramfs recovery FIT")
    destination = ROOT / "logs/boltwrt" / f"{args.target}-images.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(record, indent=2) + "\n")
    print(f"{args.target}: package manifest and {len(record['images'])} image artifacts checked; no boot test performed")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(f"boltwrt-verify: {error}", file=sys.stderr)
        sys.exit(1)
