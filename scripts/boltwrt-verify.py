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
import zlib
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


def check_rax_gpt(path):
    """Read ptgen's primary-only GPT artifact, not a full eMMC disk dump."""
    require(1024 <= path.stat().st_size <= 1024 * 1024, "invalid eMMC GPT artifact size")
    data = path.read_bytes()
    require(data[510:512] == b"\x55\xaa" and data[512:520] == b"EFI PART",
            "missing eMMC protective MBR/GPT signatures")
    revision, header_size, expected_crc, reserved = struct.unpack_from("<IIII", data, 520)
    require(revision == 0x10000 and 92 <= header_size <= 512 and reserved == 0,
            "invalid eMMC GPT primary header")
    header = bytearray(data[512:512 + header_size])
    header[16:20] = bytes(4)
    require(zlib.crc32(header) == expected_crc, "eMMC GPT header CRC mismatch")
    current, backup, first_usable, last_usable = struct.unpack_from("<QQQQ", data, 536)
    entry_lba, count, size, table_crc = struct.unpack_from("<QIII", data, 584)
    require(current == 1 and entry_lba >= 2 and 1 <= count <= 4096 and
            128 <= size <= 4096 and size % 128 == 0 and count * size <= 1024 * 1024,
            "invalid eMMC GPT table geometry")
    table_end = entry_lba * 512 + count * size
    table_sectors = (count * size + 511) // 512
    require(table_end <= len(data) and (table_end + 511) // 512 <= first_usable <= last_usable
            and last_usable + table_sectors < backup, "invalid eMMC GPT usable range/table bounds")
    table = data[entry_lba * 512:table_end]
    require(zlib.crc32(table) == table_crc, "eMMC GPT partition table CRC mismatch")
    partitions, ranges, guids = {}, [], set()
    for offset in range(0, len(table), size):
        entry = table[offset:offset + size]
        if entry[:16] == bytes(16):
            continue
        guid = entry[16:32]
        require(guid != bytes(16) and guid not in guids, "invalid/duplicate eMMC GPT partition GUID")
        guids.add(guid)
        first, last = struct.unpack_from("<QQ", entry, 32)
        require(first_usable <= first <= last <= last_usable, "eMMC GPT partition outside usable range")
        require(all(last < start or first > end for start, end in ranges), "overlapping eMMC GPT partitions")
        ranges.append((first, last))
        name = entry[56:128].decode("utf-16le").rstrip("\0")
        if name:
            require(name not in partitions, f"duplicate eMMC GPT partition name: {name}")
            partitions[name] = {"first_lba": first, "last_lba": last, "bytes": (last - first + 1) * 512}
    require({"recovery", "production"} <= partitions.keys(), "eMMC GPT missing recovery/production")
    return partitions


def fit_file_span(path):
    """Bound produced FITs by both their header and complete padded file."""
    file_bytes = path.stat().st_size
    with path.open("rb") as handle:
        header = handle.read(40)
    require(len(header) == 40, f"{path.name}: truncated FIT header")
    magic, total, structure, strings, reserve, version, compatible, _, strings_size, structure_size = struct.unpack(">10I", header)
    require(magic == 0xd00dfeed and 40 <= total <= file_bytes, f"{path.name}: invalid/truncated FIT size")
    require(version >= 17 and compatible <= 17 and 40 <= reserve < total and
            40 <= structure <= total and structure + structure_size <= total and
            40 <= strings <= total and strings + strings_size <= total,
            f"{path.name}: invalid FIT header ranges")
    # Complete file span conservatively includes external data, metadata and
    # padding, even when U-Boot's imszb reports fewer sectors. This is a budget
    # check, not a full FDT property/hash validator.
    return {"file_bytes": file_bytes, "fit_header_bytes": total,
            "file_sector_bytes": ((file_bytes + 511) // 512) * 512}


def check_rax_fit_budget(directory):
    prefix = "boltwrt-4.0.0-mediatek-filogic-cmcc_rax3000m-"
    partitions = check_rax_gpt(directory / (prefix + "emmc-gpt.bin"))
    production = fit_file_span(directory / (prefix + "squashfs-sysupgrade.itb"))
    recovery = fit_file_span(directory / (prefix + "initramfs-recovery.itb"))
    mib = 1024 * 1024
    # Locked DTS: 114MiB raw UBI. Normal U-Boot init creates two 1MiB env
    # volumes, and retains fit + recovery together when removing rootfs_data.
    env_bytes, raw_bytes, conservative_bytes = 2 * mib, 114 * mib, 104 * mib
    combined = production["file_bytes"] + recovery["file_bytes"] + env_bytes
    errors = []
    for name, span in (("production", production), ("recovery", recovery)):
        if span["file_sector_bytes"] > partitions[name]["bytes"]:
            errors.append(f"eMMC {name} FIT/file span {span['file_sector_bytes']} exceeds GPT capacity {partitions[name]['bytes']}")
    if production["file_bytes"] >= conservative_bytes:
        errors.append("RAX production exceeds conservative 104 MiB NAND budget")
    if combined > raw_bytes:
        errors.append(f"NAND production+recovery+two 1MiB env lower bound {combined} exceeds raw 114 MiB UBI {raw_bytes}")
    if combined >= conservative_bytes:
        errors.append(f"NAND coexistence lower bound {combined} exceeds conservative 104 MiB budget; UBI/bad-block capacity remains untested")
    require(not errors, "; ".join(errors))
    return {"emmc_partitions": partitions, "production": production, "recovery": recovery,
            "nand_payload_lower_bound_bytes": combined, "nand_environment_bytes": env_bytes,
            "nand_raw_ubi_bytes": raw_bytes, "nand_conservative_bytes": conservative_bytes,
            "scope": "Static conservative budget only; UBI rounding/reserves/bad blocks and device writes untested"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("x86-64", "cmcc-rax3000m"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--rax-budget-only", action="store_true",
                        help="check only RAX GPT/FIT budgets; no config/manifest/profile checks")
    args = parser.parse_args()
    if args.rax_budget_only:
        require(args.target == "cmcc-rax3000m", "--rax-budget-only requires cmcc-rax3000m")
        directory = args.directory or ROOT / "bin/targets/mediatek/filogic"
        print(json.dumps(check_rax_fit_budget(directory), indent=2))
        return
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
        record["rax_fit_budget"] = check_rax_fit_budget(directory)
        for medium in ("emmc", "nand"):
            for ddr in ("ddr3", "ddr4"):
                for kind in ("preloader.bin", "bl31-uboot.fip"):
                    path = directory / f"{prefix}{medium}-{ddr}-{kind}"
                    require(path.is_file() and path.stat().st_size > 0, f"missing hardware artifact: {path.name}")
                    record["images"][path.name] = {"bytes": path.stat().st_size, "sha256": digest(path)}
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
