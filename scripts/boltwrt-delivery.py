#!/usr/bin/env python3
"""Stage verified build provenance, matching installed APKs and checksums."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile

TARGETS = {"x86-64": ("x86/64", "x86_64"),
           "cmcc-rax3000m": ("mediatek/filogic", "aarch64_cortex-a53")}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_files(root, target, packages):
    folder, architecture = TARGETS[target]
    found = {}
    for directory in (root / "bin/packages" / architecture, root / "bin/targets" / folder / "packages"):
        for path in sorted(directory.rglob("*.apk")):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"APK is not a regular build artifact: {path}")
            if path.name in found and path.resolve() != found[path.name].resolve():
                raise ValueError(f"ambiguous APK artifact: {path.name}")
            found[path.name] = path
    result = {}
    for name, version in packages.items():
        filename = f"{name}-{version}.apk"
        if filename not in found:
            raise ValueError(f"installed package has no matching build APK: {filename}")
        result[filename] = found[filename]
    return result


def public_keys(rootfs):
    keys = {}
    with tarfile.open(rootfs, "r:*") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.parts[:3] != ("etc", "apk", "keys") or not member.isfile():
                continue
            if len(path.parts) != 4 or path.is_absolute() or ".." in path.parts or member.size > 65536:
                raise ValueError("unexpected APK public key archive member")
            content = archive.extractfile(member).read()
            if b"PUBLIC KEY-----" not in content or b"PRIVATE KEY" in content or path.name in keys:
                raise ValueError("invalid/duplicate APK public key in actual rootfs")
            keys[path.name] = content
    if not keys:
        raise ValueError("actual rootfs contains no APK public keys")
    return keys


def add_bytes(archive, name, content):
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = 0o644
    member.mtime = 0
    archive.addfile(member, io.BytesIO(content))


def stage(root, target):
    folder, _ = TARGETS[target]
    directory = root / "bin/targets" / folder
    images = json.loads((root / f"logs/boltwrt/{target}-images.json").read_text())
    software = json.loads((root / f"logs/boltwrt/{target}-software.json").read_text())
    lock_path = root / "config/source-lock.json"
    lock_hash = sha256(lock_path)
    if images["source_lock_sha256"] != lock_hash or software["source_lock_sha256"] != lock_hash:
        raise ValueError("artifact checks do not match the current source lock")
    for name, expected in images["images"].items():
        if PurePosixPath(name).name != name or sha256(directory / name) != expected["sha256"]:
            raise ValueError(f"verified image has changed: {name}")
    rootfs_name = software["rootfs"]
    if PurePosixPath(rootfs_name).name != rootfs_name:
        raise ValueError("invalid checked rootfs archive name")
    rootfs = directory / rootfs_name
    manifests = list(directory.glob("boltwrt-*.manifest"))
    if rootfs.is_symlink() or not rootfs.is_file() or len(manifests) != 1:
        raise ValueError("expected a checked actual rootfs and one package manifest")
    if sha256(rootfs) != software["rootfs_sha256"] or sha256(manifests[0]) != software["manifest_sha256"]:
        raise ValueError("checked rootfs or manifest has changed")
    archives = package_files(root, target, images["packages"])
    keys = public_keys(rootfs)
    prefix = f"boltwrt-{images['version']}-{target}"
    bundle = directory / (prefix + "-installed-packages.tar.gz")
    with tempfile.TemporaryDirectory(prefix="boltwrt-delivery-", dir=root / "tmp") as temporary:
        temporary = Path(temporary)
        key_dir = temporary / "keys"
        key_dir.mkdir()
        for name, content in keys.items():
            (key_dir / name).write_bytes(content)
        apk = root / "staging_dir/host/bin/apk"
        signing_key = root / "private-key.pem"
        if not signing_key.is_file() or signing_key.is_symlink():
            raise ValueError("the build signing key is unavailable for delivery APK signatures")
        # OpenWrt signs repository indexes. Standalone file uploads require
        # individual APK signatures too; add these only to delivery copies.
        copy_dir = temporary / "apk"
        copy_dir.mkdir()
        copies = {}
        for name, path in archives.items():
            copies[name] = copy_dir / name
            shutil.copyfile(path, copies[name])
        signed = subprocess.run([str(apk), "adbsign", "--allow-untrusted", "--reset-signatures", "--sign-key", str(signing_key),
                                 *map(str, copies.values())], capture_output=True, text=True)
        if signed.returncode:
            raise ValueError("standalone delivery APK signing failed:\n" + signed.stdout + signed.stderr)
        verify = subprocess.run([str(apk), "--no-network", "--repositories-file", "/dev/null",
                                 "--keys-dir", str(key_dir), "verify", *map(str, copies.values())],
                                capture_output=True, text=True)
        if verify.returncode:
            raise ValueError("a matching APK failed checksum/signature verification with the image's public keys:\n" + verify.stdout + verify.stderr)
        inventory = {name: {"bytes": path.stat().st_size, "sha256": sha256(path),
                            "build_apk_sha256": sha256(archives[name])}
                     for name, path in copies.items()}
        temporary_bundle = temporary / "packages.tar.gz"
        with temporary_bundle.open("wb") as output, gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name, path in sorted(copies.items()):
                    member = tarfile.TarInfo("apk/" + name)
                    member.size = path.stat().st_size
                    member.mode = 0o644
                    with path.open("rb") as stream:
                        archive.addfile(member, stream)
                for name, content in sorted(keys.items()):
                    add_bytes(archive, "keys/" + name, content)
                add_bytes(archive, "packages.json", (json.dumps(inventory, indent=2) + "\n").encode())
        os.replace(temporary_bundle, bundle)
    prepared = json.loads((root / "logs/prepare-sources.json").read_text())
    if prepared["source_lock_sha256"] != lock_hash:
        raise ValueError("source preparation evidence differs from the current source lock")
    record = {
        "target": target, "version": images["version"],
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "delivery_tool_sha256": sha256(Path(__file__)),
        "source_lock_sha256": lock_hash, "source_lock": json.loads(lock_path.read_text()),
        "feeds": {name: {key: value for key, value in info.items() if key != "effective_url"}
                  for name, info in prepared["feeds"].items()},
        "patched_files_sha256": prepared["patched_files_sha256"],
        "software": software, "images": images["images"],
        "apk_bundle": {"name": bundle.name, "sha256": sha256(bundle), "packages": len(inventory),
                       "signing": "build-key signatures added to standalone delivery copies",
                       "verified_with_actual_image_public_keys": True,
                       "public_keys_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in keys.items()}},
        "scope": "build/image/payload checks and installed APK delivery; runtime acceptance is recorded separately",
    }
    receipt = directory / (prefix + "-build.json")
    encoded = json.dumps(record, indent=2) + "\n"
    receipt.write_text(encoded)
    (root / f"logs/boltwrt/{target}-delivery.json").write_text(encoded)
    checksum = directory / (prefix + "-SHA256SUMS")
    files = sorted(path for path in directory.glob("boltwrt-*") if path.is_file() and path != checksum)
    if any(path.is_symlink() for path in files):
        raise ValueError("delivery artifacts must not be symlinks")
    checksum.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in files))
    print(f"{target}: staged {len(inventory)} signed installed APKs, build provenance and checksums")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", required=True, type=Path)
    parser.add_argument("--target", required=True, choices=TARGETS)
    args = parser.parse_args()
    stage(args.build_root.resolve(strict=True), args.target)


if __name__ == "__main__":
    main()
