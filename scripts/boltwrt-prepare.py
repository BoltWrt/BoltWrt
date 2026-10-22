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
        print(f"Fetching locked source {repo} @ {commit}", flush=True)
        repo.parent.mkdir(parents=True, exist_ok=True)
        # Publish only a complete checkout. A failed fetch leaves no unborn
        # destination repository that a later run could mistake for user work.
        with tempfile.TemporaryDirectory(prefix=f".{repo.name}.fetch-", dir=repo.parent) as temporary:
            staged = Path(temporary) / "checkout"
            run(["git", "init", "--quiet", staged], repo.parent)
            git(staged, "remote", "add", "origin", url)
            git(staged, "fetch", "--depth=1", "--no-tags", "origin", commit)
            git(staged, "checkout", "--detach", "--quiet", commit)
            if git(staged, "rev-parse", "HEAD") != commit:
                raise PrepareError(f"{repo}: fetched object does not match lock")
            if repo.exists() or repo.is_symlink():
                raise PrepareError(f"destination appeared during fetch; refusing to replace: {repo}")
            staged.rename(repo)
    else:
        print(f"Checking locked source {repo} @ {commit}", flush=True)
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
    print(f"Locked source ready: {repo} @ {commit}", flush=True)


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


def submodule_patches(repo, spec, children):
    result = {}
    if "net/openwrt-smartdns" in children:
        result["net/openwrt-smartdns"] = {name: list(edits) for name, edits in SMARTDNS_PATCH.items()}
    if "patch_manifest" not in spec:
        return result
    if not isinstance(spec["patch_manifest"], str):
        raise PrepareError("patch_manifest must be a repository-relative path")
    manifest_path = str(safe_relative(spec["patch_manifest"]))
    # Read the committed object, never an ignored or substituted working file.
    manifest = json.loads(run(["git", "show", f"HEAD:{manifest_path}"], repo))
    if not isinstance(manifest, dict) or set(manifest) != {"submodules"} or not isinstance(manifest["submodules"], dict):
        raise PrepareError("unsupported submodule patch manifest")
    for path, item in manifest["submodules"].items():
        safe_relative(path)
        if (not isinstance(item, dict) or set(item) != {"commit", "files"}
                or path not in children or item["commit"] != children[path]):
            raise PrepareError(f"patch manifest source does not match locked gitlink: {path}")
        if not isinstance(item["files"], dict) or not item["files"]:
            raise PrepareError(f"patch manifest has no files: {path}")
        for filename, replacements in item["files"].items():
            safe_relative(filename)
            if (not isinstance(replacements, list) or not replacements
                    or any(not isinstance(edit, list) or len(edit) != 2
                           or not all(isinstance(text, str) for text in edit) or not edit[0]
                           for edit in replacements)):
                raise PrepareError(f"invalid exact replacements: {path}/{filename}")
            result.setdefault(path, {}).setdefault(filename, []).extend(replacements)
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


def verify_package_tree(root, allowed):
    """Enumerate build-visible paths, including unknown feeds and directory aliases."""
    base = root / "package/feeds"
    if base.is_symlink():
        raise PrepareError(f"unknown package/feeds directory symlink: {base}")
    if not base.exists():
        return

    def visit(path, ancestors):
        if path.is_symlink() and path in allowed:
            if path.resolve() not in allowed[path]:
                raise PrepareError(f"unknown provider symlink: {path}")
            return  # Locked source contents were checked separately.
        if path.name == "Makefile" and path.is_file():
            raise PrepareError(f"unknown build source in package/feeds: {path.parent}")
        if path.is_dir():
            identity = (path.stat().st_dev, path.stat().st_ino)
            if identity in ancestors:
                raise PrepareError(f"directory cycle in package/feeds: {path}")
            for child in path.iterdir():
                visit(child, ancestors | {identity})

    visit(base, set())


def locked_source_links(root, repositories):
    """Pre-index allowlist from tracked Makefiles, never arbitrary working files."""
    allowed = {}
    for repo, _ in repositories:
        feed = repo.relative_to(root / "feeds").parts[0]
        for name in git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines():
            if Path(name).name == "Makefile":
                source = repo / Path(name).parent
                link = root / "package/feeds" / feed / source.name
                allowed.setdefault(link, set()).add(source.resolve())
    return allowed


def verify_links(root, winners, rejected, remove_rejected=False, require_winners=False,
                 required_sources=()):
    allowed = {root / "package/feeds" / feed / Path(source).name: {(root / source).resolve()}
               for source, feed in {**winners, **rejected}.items()}
    verify_package_tree(root, allowed)
    for source, feed in {**winners, **rejected}.items():
        link = root / "package/feeds" / feed / Path(source).name
        if link.is_symlink():
            if link.resolve() != (root / source).resolve():
                raise PrepareError(f"unknown provider symlink: {link}")
            if source in rejected and remove_rejected:
                link.unlink()  # Only a positively identified locked competing provider.
        elif link.exists():
            raise PrepareError(f"refusing non-symlink provider path: {link}")
        elif source in winners and (require_winners or source in required_sources):
            raise PrepareError(f"selected provider was not installed: {source}")


def positive_package_choices(root):
    files = [root / "config/common.txt", *sorted((root / "config/targets").glob("*.txt"))]
    if len(files) < 2 or not files[0].is_file():
        raise PrepareError("product common.txt and target fragments are required to select feed roots")
    choices = set()
    for path in files:
        for line in path.read_text().splitlines():
            match = re.fullmatch(r"CONFIG_PACKAGE_([A-Za-z0-9_.+-]+)=[ym]", line)
            if match:
                choices.add(match[1])
    return choices


def product_roots(indexes, winners, choices):
    return {source: feed for source, feed in winners.items()
            if feed == "BoltWrt" or indexes[feed][source] & choices}


def check_registration_scope(root, scope, preindex_links):
    """Preserve older full-registration states instead of guessing their dependency closure.

    Completed product-scoped preparations repeat in place. Changed product roots,
    legacy full registration, and partially installed unrecorded states require a
    clean build directory; their existing source links are never reset here.
    """
    verify_package_tree(root, preindex_links)
    evidence_path = root / "logs/prepare-sources.json"
    existing = any(link.is_symlink() for link in preindex_links)
    if not existing:
        return
    if not evidence_path.is_file():
        raise PrepareError("unrecorded feed registration preserved; use a clean build directory")
    previous = json.loads(evidence_path.read_text())
    if previous.get("registration_scope") != scope:
        raise PrepareError("legacy or changed product registration preserved; use a clean build directory")
    previous_sources = previous.get("selected_providers", {})
    allowed = {root / "package/feeds" / feed / Path(source).name: {(root / source).resolve()}
               for source, feed in previous_sources.items()}
    verify_package_tree(root, allowed)
    for link in allowed:
        if not link.is_symlink():
            raise PrepareError(f"recorded provider is missing; registration preserved: {link}")


def reject_kconfig_errors(output):
    diagnostic = re.search(
        r"(?m)^(?:[^\n]*:[0-9]+:error:|(?:error:\s*)?recursive dependency detected!\s*$)", output)
    if diagnostic:
        raise PrepareError("Kconfig reported an error despite command success:\n" + output)


def checked_make(root, env, *args):
    output = run(["make", *args], root, env)
    print(output, end="", flush=True)
    reject_kconfig_errors(output)


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
    reject_kconfig_errors(output)
    if failures.exists() and failures.stat().st_size != before:
        raise PrepareError(f"a make command inside scripts/feeds failed; see {failures}")


def prepare(root, local_feed=None):
    lock_path = root / "config/source-lock.json"
    lock = json.loads(lock_path.read_text())
    choices = positive_package_choices(root)
    scope = {"mode": "product-roots-v1", "positive_package_choices": sorted(choices),
             "source_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest()}
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
            child_patch_sets = submodule_patches(repo, spec, children)
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
                child_patches = child_patch_sets.get(path, {})
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
    # feeds update -i can itself run make defconfig. Reject stray build sources
    # before that first metadata scan, then tighten to the actual feed indexes.
    check_registration_scope(root, scope, locked_source_links(root, pending))
    feeds_command(root, env, failures, "update", "-i", *lock["feeds"])
    indexes = {name: source_index(root, name) for name in lock["feeds"]}
    winners, rejected = provider_plan(indexes)
    roots = product_roots(indexes, winners, choices)
    verify_links(root, winners, rejected)
    verify_links(root, winners, rejected, remove_rejected=True)
    # Register declared products and every custom source, not unrelated official
    # applications. feeds retains responsibility for recursive dependencies and
    # virtual providers; variants inside a required source remain intact.
    for feed in ["BoltWrt"] + [name for name in indexes if name != "BoltWrt"]:
        names = sorted(Path(source).name for source, owner in roots.items() if owner == feed)
        if names:
            feeds_command(root, env, failures, "install", "-p", feed, *names)
    verify_links(root, winners, rejected, remove_rejected=True, required_sources=roots)
    # Rebuild metadata after removing identified competing dependency symlinks.
    checked_make(root, env, "-s", "prepare-tmpinfo", "OPENWRT_BUILD=")
    if (root / ".config").exists():
        checked_make(root, env, "defconfig")
    verify_links(root, winners, rejected, required_sources=roots)
    installed = {source: feed for source, feed in winners.items()
                 if (root / "package/feeds" / feed / Path(source).name).is_symlink()}
    evidence = {"source_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
                "feeds": records, "patched_files_sha256": patch_hashes,
                "registration_scope": scope, "requested_root_providers": roots,
                "selected_providers": installed, "excluded_overlapping_sources": rejected}
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
