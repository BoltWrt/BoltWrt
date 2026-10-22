#!/usr/bin/env python3
"""Prepare the locked release feeds without resetting unknown work."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


class PrepareError(RuntimeError):
    pass


PATCHES = {
    "luci": {
        "modules/luci-base/root/etc/config/luci": [
            ("option mediaurlbase /luci-static/bootstrap", "option mediaurlbase /luci-static/argon")],
        "collections/luci-nginx/Makefile": [
            ("\t+luci-theme-bootstrap \\\n", "")],
    },
    "packages": {
        "net/nginx-util/src/nginx-ssl-util.hpp": [
            ("/C=ZZ/ST=Somewhere/L=None/CN=OpenWrt/O=OpenWrt",
             "/C=CN/ST=Beijing/L=Beijing/CN=BoltWrt/O=BoltWrt"),
            ("constexpr auto validity_days = 3 * (365 + 31);", "constexpr auto validity_days = 3650;")],
    },
}
SMARTDNS_PATCH = {"Makefile": [
    ("include ../../lang/rust/rust-package.mk",
     "include $(TOPDIR)/feeds/packages/lang/rust/rust-package.mk")]}


def run(argv, cwd, env=None):
    command_env = dict(os.environ if env is None else env,
                       GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    result = subprocess.run([str(a) for a in argv], cwd=cwd, env=command_env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        raise PrepareError(f"command failed ({result.returncode}): {argv!r}\n{result.stdout}")
    return result.stdout


def git(repo, *args):
    return run(["git", "-C", repo, *args], repo).strip()


def exact_patch(original, replacements, label):
    result = original
    for old, new in replacements:
        if result.count(old) != 1:
            raise PrepareError(f"{label}: expected exactly one locked-source match for {old!r}")
        result = result.replace(old, new, 1)
    return result


def atomic_write(path, data):
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".boltwrt-", delete=False) as f:
        temporary = Path(f.name)
        f.write(data)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def safe_relative(value):
    p = Path(value)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise PrepareError(f"unsafe relative path: {value!r}")
    return p


def checkout(repo, url, commit, allowed_origins=()):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PrepareError(f"not a full commit SHA: {commit!r}")
    if repo.is_symlink():
        raise PrepareError(f"feed checkout must not be a symlink: {repo}")
    if not repo.exists():
        repo.mkdir(parents=True)
        run(["git", "init", "--quiet", repo], repo.parent)
        git(repo, "remote", "add", "origin", url)
        git(repo, "fetch", "--depth=1", "--no-tags", "origin", commit)
        git(repo, "checkout", "--detach", "--quiet", commit)
    else:
        if not (repo / ".git").exists():
            raise PrepareError(f"refusing existing non-repository directory: {repo}")
        if Path(git(repo, "rev-parse", "--show-toplevel")).resolve() != repo.resolve():
            raise PrepareError(f"unexpected repository root: {repo}")
        if git(repo, "rev-parse", "HEAD") != commit:
            raise PrepareError(f"{repo}: HEAD differs from lock; refusing checkout/reset")
        origin = git(repo, "remote", "get-url", "origin")
        if origin not in {url, *allowed_origins}:
            raise PrepareError(f"{repo}: unexpected origin {origin!r}")
    if git(repo, "rev-parse", "HEAD") != commit:
        raise PrepareError(f"{repo}: fetched object does not match lock")


def check_worktree(repo, patches, children=()):
    """Allow only pristine files or complete, reproducible patches from locked HEAD."""
    if git(repo, "diff", "--cached", "--name-only"):
        raise PrepareError(f"{repo}: staged changes are not allowed")
    if git(repo, "ls-files", "--others", "--exclude-standard"):
        raise PrepareError(f"{repo}: unknown untracked files")
    if git(repo, "diff", "--summary"):
        raise PrepareError(f"{repo}: unexpected mode/create/delete changes")
    changed = set(git(repo, "diff", "--name-only", "HEAD").splitlines())
    unknown = changed - set(patches) - set(children)
    if unknown:
        raise PrepareError(f"{repo}: unknown modifications: {sorted(unknown)}")
    expected = {}
    for name, replacements in patches.items():
        p = repo / safe_relative(name)
        if p.is_symlink() or not p.is_file():
            raise PrepareError(f"expected regular patch target: {p}")
        original = run(["git", "show", f"HEAD:{name}"], repo)
        result = exact_patch(original, replacements, str(p))
        if p.read_bytes() not in {original.encode(), result.encode()}:
            raise PrepareError(f"{p}: content is neither locked source nor known patch")
        expected[name] = result.encode()
    return expected


def validate_feed_config(root, lock):
    expected = {name: (spec["type"], spec["url"] + "^" + spec["commit"])
                for name, spec in lock["feeds"].items()}
    for filename in ("feeds.conf.default", "feeds.conf"):
        path = root / filename
        if not path.exists():
            continue
        found = {}
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 3 or parts[1] in found:
                raise PrepareError(f"{filename}: unsupported or duplicate feed line: {line}")
            found[parts[1]] = (parts[0], parts[2])
        if found != expected:
            raise PrepareError(f"{filename} differs from config/source-lock.json")


def submodule_urls(repo):
    output = git(repo, "config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$")
    result = {}
    for line in output.splitlines():
        key, path = line.split(None, 1)
        result[path] = git(repo, "config", "--file", ".gitmodules", "--get", key[:-4] + "url")
    return result


def source_index(root, feed):
    path = root / "feeds" / (feed + ".index")
    if not path.is_file():
        raise PrepareError(f"missing feed index: {path}")
    sources = {}
    current = None
    for line in path.read_text().splitlines():
        if line.startswith("Source-Makefile: "):
            source = Path(line.partition(": ")[2]).parent
            if source.parts[:2] != ("feeds", feed) or ".." in source.parts:
                raise PrepareError(f"unexpected source path in {path}: {source}")
            current = str(source)
            sources.setdefault(current, set())
        elif line.startswith("Package: ") and current:
            sources[current].add(line.partition(": ")[2])
    if not sources:
        raise PrepareError(f"empty package source index: {path}")
    return sources


def provider_plan(indexes):
    """Choose by binary package names as source directory names can differ."""
    winners, rejected, claimed = {}, {}, set()
    order = ["BoltWrt"] + [name for name in indexes if name != "BoltWrt"]
    for feed in order:
        for source, packages in indexes[feed].items():
            if packages & claimed:
                rejected[source] = feed
            else:
                winners[source] = feed
                claimed.update(packages)
    return winners, rejected


def verify_links(root, winners, rejected, remove_rejected=False, require_winners=False):
    for source, feed in {**winners, **rejected}.items():
        link = root / "package/feeds" / feed / Path(source).name
        if link.is_symlink():
            if link.resolve() != (root / source).resolve():
                raise PrepareError(f"unknown provider symlink: {link}")
            if source in rejected and remove_rejected:
                link.unlink()  # Only a positively identified locked competing provider.
        elif link.exists():
            raise PrepareError(f"refusing non-symlink provider path: {link}")
        elif require_winners and source in winners:
            raise PrepareError(f"selected provider was not installed: {source}")


def make_environment(root):
    """feeds' Perl code ignores several child make statuses; retain them independently."""
    real_make = shutil.which("gmake") or shutil.which("make")
    if not real_make or not run([real_make, "--version"], root).startswith("GNU Make"):
        raise PrepareError("GNU Make is required")
    tools = root / "logs/prepare-tools"
    tools.mkdir(parents=True, exist_ok=True)
    failures = tools / "make-failures.log"
    wrapper = ('#!' + sys.executable + '\nimport os, subprocess, sys\n'
               'r = subprocess.run([os.environ["BOLTWRT_REAL_MAKE"], *sys.argv[1:]])\n'
               'if r.returncode:\n'
               ' with open(os.environ["BOLTWRT_MAKE_FAILURES"], "a") as f: f.write(repr(sys.argv[1:]) + "\\n")\n'
               'sys.exit(r.returncode)\n').encode()
    for name in ("make", "gmake"):
        path = tools / name
        if path.exists() and path.read_bytes() != wrapper:
            raise PrepareError(f"unknown make wrapper: {path}")
        atomic_write(path, wrapper)
        path.chmod(0o755)
    env = dict(os.environ, BOLTWRT_REAL_MAKE=real_make,
               BOLTWRT_MAKE_FAILURES=str(failures),
               PATH="logs/prepare-tools:" + os.environ["PATH"])
    return env, failures


def feeds_command(root, env, failures, *args):
    before = failures.stat().st_size if failures.exists() else 0
    output = run(["./scripts/feeds", *args], root, env)
    print(output, end="", flush=True)
    if failures.exists() and failures.stat().st_size != before:
        raise PrepareError(f"a make command inside scripts/feeds failed; see {failures}")


def prepare(root, local_feed=None):
    lock_path = root / "config/source-lock.json"
    lock = json.loads(lock_path.read_text())
    if "BoltWrt" not in lock["feeds"]:
        raise PrepareError("lock has no BoltWrt feed")
    validate_feed_config(root, lock)
    if local_feed:
        local_feed = str(local_feed.resolve(strict=True))
        git(Path(local_feed), "cat-file", "-e", lock["feeds"]["BoltWrt"]["commit"] + "^{commit}")
    records, pending = {}, []
    for name, spec in lock["feeds"].items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", name) or spec["type"] != "src-git":
            raise PrepareError(f"unsupported feed entry: {name}")
        repo = root / "feeds" / name
        url = local_feed if name == "BoltWrt" and local_feed else spec["url"]
        checkout(repo, url, spec["commit"], (spec["url"],))
        children = lock.get("submodules", {}).get(name, {})
        expected = check_worktree(repo, PATCHES.get(name, {}), children)
        git(repo, "checkout", "--detach", "--quiet", spec["commit"])
        if git(repo, "remote", "get-url", "origin") != url:
            git(repo, "remote", "set-url", "origin", url)
        pending.append((repo, expected))
        records[name] = {"commit": git(repo, "rev-parse", "HEAD"), "url": spec["url"], "effective_url": url}
        if children:
            urls = submodule_urls(repo)
            if set(urls) != set(children):
                raise PrepareError(f"{name}: .gitmodules paths differ from lock")
            records[name]["submodules"] = {}
            for path, sha in children.items():
                entry = git(repo, "ls-tree", "HEAD", "--", path)
                if entry.split()[:3] != ["160000", "commit", sha]:
                    raise PrepareError(f"{name}/{path}: gitlink differs from lock")
                git(repo, "submodule", "init", "--", path)
                child = repo / safe_relative(path)
                # Git represents a new submodule by an empty directory.
                if child.is_dir() and not any(child.iterdir()):
                    child.rmdir()
                checkout(child, urls[path], sha)
                child_patches = SMARTDNS_PATCH if path == "net/openwrt-smartdns" else {}
                pending.append((child, check_worktree(child, child_patches)))
                records[name]["submodules"][path] = git(child, "rev-parse", "HEAD")
    patch_hashes = {}
    for repo, files in pending:
        for name, content in files.items():
            path = repo / name
            if path.read_bytes() != content:
                atomic_write(path, content)
            patch_hashes[str(path.relative_to(root))] = hashlib.sha256(content).hexdigest()
    env, failures = make_environment(root)
    feeds_command(root, env, failures, "update", "-i", *lock["feeds"])
    indexes = {name: source_index(root, name) for name in lock["feeds"]}
    winners, rejected = provider_plan(indexes)
    verify_links(root, winners, rejected)
    verify_links(root, winners, rejected, remove_rejected=True)
    # Do not feed overlapping official sources to install -a: smartdns and
    # openwrt-smartdns differ as source names but provide the same binary package.
    for feed in ["BoltWrt"] + [name for name in indexes if name != "BoltWrt"]:
        names = sorted(Path(source).name for source, owner in winners.items() if owner == feed)
        if names:
            feeds_command(root, env, failures, "install", "-p", feed, *names)
    verify_links(root, winners, rejected, remove_rejected=True, require_winners=True)
    # Rebuild metadata after removing identified competing dependency symlinks.
    run(["make", "-s", "prepare-tmpinfo", "OPENWRT_BUILD="], root, env)
    verify_links(root, winners, rejected, require_winners=True)
    if (root / ".config").exists():
        run(["make", "defconfig"], root, env)
    evidence = {"source_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
                "feeds": records, "patched_files_sha256": patch_hashes,
                "selected_providers": winners, "excluded_overlapping_sources": rejected}
    (root / "logs").mkdir(exist_ok=True)
    atomic_write(root / "logs/prepare-sources.json", (json.dumps(evidence, indent=2) + "\n").encode())
    print("Locked feeds prepared; evidence: logs/prepare-sources.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--local-feed", type=Path, help="local BoltWrt repository containing the locked commit")
    args = parser.parse_args()
    try:
        prepare(args.root.resolve(strict=True), args.local_feed)
    except (PrepareError, OSError, ValueError, KeyError) as error:
        print(f"prepare failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
