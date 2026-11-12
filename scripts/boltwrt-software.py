#!/usr/bin/env python3
"""Check packaged software and Go build information in an actual rootfs archive."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile

GO_PROGRAMS = {
    "usr/bin/xray", "usr/bin/sing-box",
    "usr/bin/geoview", "usr/bin/v2ray-plugin", "usr/bin/frpc",
}
VERSION_PACKAGES = {
    "passwall2": ("luci-app-passwall2",),
    "xray": ("xray-core",),
    "argon": ("luci-theme-argon",),
    "smartdns_luci": ("luci-app-smartdns",),
    "shadowsocksr-libev": ("shadowsocksr-libev-ssr-local", "shadowsocksr-libev-ssr-redir"),
}


def check_product_policy(archive):
    forbidden = {
        'usr/bin/hysteria', 'usr/bin/ss-local', 'usr/bin/ss-redir', 'usr/bin/ss-server',
        'usr/bin/naive', 'usr/bin/tuic-client',
        'etc/uci-defaults/94-boltwrt-migration-cleanup',
        'www/luci-static/resources/view/argon-config.js',
        'www/luci-static/resources/argon-appearance.js',
        'usr/share/luci/menu.d/luci-app-argon-config.json',
        'usr/share/rpcd/acl.d/luci-app-argon-config.json',
        'usr/share/rpcd/acl.d/luci-theme-argon.json',
        'usr/libexec/rpcd/luci.argon', 'usr/libexec/rpcd/luci.argon_update',
        'usr/libexec/rpcd/luci.argon_appearance', 'usr/libexec/rpcd/luci.argon_branding',
    }
    for role, names in {
        'client': ('5_hysteria2.lua', '6_ss-libev.lua', '7_naive.lua', '8_tuic.lua'),
        'server': ('5_hysteria2.lua', '6_ss-libev.lua'),
    }.items():
        forbidden.update('usr/lib/lua/luci/model/cbi/passwall2/' + role + '/type/' + name for name in names)
    forbidden.update('usr/lib/lua/luci/passwall2/' + name for name in
                     ('util_hysteria2.lua', 'util_naiveproxy.lua', 'util_tuic.lua'))
    required = {
        'etc/config/luci': b'/luci-static/argon',
        'www/luci-static/resources/view/system/system.js': None,
        'usr/share/ucode/luci/template/themes/argon/header.ut': None,
        'usr/share/ucode/luci/template/themes/argon/page_scripts.ut': None,
        'www/luci-static/argon/css/cascade.css': None,
        'usr/libexec/rpcd/luci.argon_wallpaper': None,
    }
    contents = {}
    with tarfile.open(archive, 'r:*') as stream:
        for member in stream:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('unsafe product policy archive path')
            name = str(path)
            if name in forbidden or name.startswith('etc/boltwrt/migrations/'):
                raise ValueError(f'retired product payload remains: {name}')
            if name in required:
                if name in contents or not member.isfile() or member.size > 4 * 1024**2:
                    raise ValueError(f'invalid product policy payload: {name}')
                contents[name] = stream.extractfile(member).read()
    for name, token in required.items():
        if not contents.get(name) or (token and token not in contents[name]):
            raise ValueError(f'missing required fixed-theme payload: {name}')
    if b'_mediaurlbase' in contents['www/luci-static/resources/view/system/system.js']:
        raise ValueError('theme selector remains in actual system view')
    for name in ('header.ut', 'page_scripts.ut'):
        if b'argon-appearance' in contents['usr/share/ucode/luci/template/themes/argon/' + name]:
            raise ValueError('appearance selector remains in actual theme template')
    return {'upstream_backends_only': True, 'fixed_theme': 'argon',
            'theme_controls': False, 'persistent_migration_backup': False,
            'test_cleanup_hook': False}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def rootfs_archive(directory):
    candidates = [path for path in directory.glob("boltwrt-*-rootfs.tar.gz")
                  if not path.name.endswith("-targz-rootfs.tar.gz")]
    if len(candidates) != 1 or candidates[0].is_symlink():
        raise ValueError("expected one regular canonical rootfs archive")
    rootfs = candidates[0]
    expected_alias = rootfs.with_name(rootfs.name.removesuffix("-rootfs.tar.gz") + "-targz-rootfs.tar.gz")
    for alias in directory.glob("boltwrt-*-targz-rootfs.tar.gz"):
        if alias != expected_alias or alias.is_symlink() or sha256(alias) != sha256(rootfs):
            raise ValueError("rootfs alias differs from the canonical archive")
    return rootfs


def check_versions(manifest, policy):
    packages = {}
    for line in Path(manifest).read_text().splitlines():
        name, separator, version = line.partition(" - ")
        if not separator or not name or not version or name in packages:
            raise ValueError("invalid or duplicate manifest package")
        packages[name] = version
    checked = {}
    for component, names in VERSION_PACKAGES.items():
        expected = policy[component]["version"]
        for name in names:
            actual = packages.get(name)
            if actual is None or actual.split("-r", 1)[0] != expected:
                raise ValueError(f"{name}: expected {expected}, found {actual}")
            release = policy[component].get("release")
            if release is not None and actual != f"{expected}-r{release}":
                raise ValueError(f"{name}: expected downstream release {expected}-r{release}, found {actual}")
            checked[name] = actual
    for component in ("geoip", "geosite"):
        name = "v2ray-" + component
        actual = packages.get(name)
        if actual is None or actual.split("-r", 1)[0] != policy[component] + ".1":
            raise ValueError(f"{name}: unexpected geodata version {actual}")
        checked[name] = actual
    return checked


def check_go_archive(archive, go, expected, architecture=None):
    programs = {}
    with tempfile.TemporaryDirectory(prefix="boltwrt-buildinfo-") as temporary:
        payload = Path(temporary) / "payload"
        with tarfile.open(archive, "r:*") as stream:
            for member in stream:
                if not member.isfile():
                    continue
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("unexpected rootfs archive path")
                name = str(path)
                with stream.extractfile(member) as source:
                    header = source.read(4)
                    if header != b"\x7fELF":
                        continue
                    content = header + source.read()
                # Inspect bytes without extracting OS symlinks or executing
                # any target program on the host architecture.
                if b"\xff Go buildinf:" not in content:
                    continue
                if name in programs:
                    raise ValueError(f"duplicate Go payload {name}")
                payload.write_bytes(content)
                result = subprocess.run([str(go), "version", "-m", str(payload)],
                                        check=True, capture_output=True, text=True)
                match = re.search(r": (go[^\s]+)\s*$", result.stdout.splitlines()[0])
                version = match[1] if match else None
                if version != "go" + expected:
                    raise ValueError(f"{name}: expected go{expected}, found {version}")
                environment = {}
                for line in result.stdout.splitlines()[1:]:
                    setting = re.match(r"\s*build\s+(GOOS|GOARCH|GOAMD64|GOARM64|CGO_ENABLED)=(.*)$", line)
                    if setting:
                        environment[setting[1]] = setting[2]
                if architecture:
                    machine = 62 if architecture == "amd64" else 183
                    if (content[4:6] != b"\x02\x01" or int.from_bytes(content[18:20], "little") != machine or
                            environment.get("GOARCH") != architecture or environment.get("GOOS") != "linux"):
                        raise ValueError(f"{name}: Go ELF architecture differs from linux/{architecture}")
                    baseline_key, baseline_isa = ("GOAMD64", "v1") if architecture == "amd64" else ("GOARM64", "v8.0")
                    if environment.get(baseline_key) != baseline_isa:
                        raise ValueError(f"{name}: expected portable {baseline_key}={baseline_isa}")
                programs[name] = {"go_version": version,
                                  "sha256": hashlib.sha256(content).hexdigest(),
                                  "bytes": len(content), "build_environment": environment}
        missing = GO_PROGRAMS - programs.keys()
        if missing:
            raise ValueError(f"missing required Go program build information: {sorted(missing)}")
    return programs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path)
    parser.add_argument("--target", choices=("x86-64", "cmcc-rax3000m"))
    parser.add_argument("--rootfs", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--source-lock", type=Path)
    parser.add_argument("--go-tool", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.build_root:
        root = args.build_root.resolve(strict=True)
        if not args.target:
            parser.error("--build-root requires --target")
        folder = "x86/64" if args.target == "x86-64" else "mediatek/filogic"
        directory = root / "bin/targets" / folder
        def unique(pattern):
            matches = sorted(directory.glob(pattern))
            if len(matches) != 1:
                raise ValueError(f"expected one {pattern} in {directory}, found {len(matches)}")
            return matches[0]
        args.rootfs = args.rootfs or rootfs_archive(directory)
        args.manifest = args.manifest or unique("boltwrt-*.manifest")
        args.policy = args.policy or root / "feeds/BoltWrt/compat/software-lock.json"
        args.source_lock = args.source_lock or root / "config/source-lock.json"
        args.output = args.output or root / f"logs/boltwrt/{args.target}-software.json"
        if not args.go_tool:
            expected = json.loads(args.source_lock.read_text())["toolchains"]["golang"]
            for candidate in sorted((root / "staging_dir/hostpkg/lib").glob("go*/bin/go")):
                result = subprocess.run([str(candidate), "version"], capture_output=True, text=True)
                if result.returncode == 0 and result.stdout.split()[2] == "go" + expected:
                    args.go_tool = candidate
                    break
    if any(getattr(args, name) is None for name in
           ("rootfs", "manifest", "policy", "source_lock", "go_tool", "output")):
        parser.error("provide a prepared --build-root or all explicit input and output paths")
    policy = json.loads(args.policy.read_text())
    baseline = json.loads(args.source_lock.read_text())["toolchains"]["golang"]
    packages = check_versions(args.manifest, policy)
    architecture = {"x86-64": "amd64", "cmcc-rax3000m": "arm64"}.get(args.target)
    programs = check_go_archive(args.rootfs, args.go_tool, baseline, architecture)
    product_policy = check_product_policy(args.rootfs)
    record = {"rootfs": args.rootfs.name, "rootfs_sha256": sha256(args.rootfs),
              "manifest_sha256": sha256(args.manifest),
              "policy_sha256": sha256(args.policy),
              "source_lock_sha256": sha256(args.source_lock),
              "go_baseline": baseline, "packages": packages, "go_programs": programs,
              "product_policy": product_policy,
              "scope": "actual packaged payload bytes and build information; no runtime acceptance"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n")
    print(f"Verified {len(packages)} software versions and {len(programs)} Go payloads ({baseline})")


if __name__ == "__main__":
    main()
