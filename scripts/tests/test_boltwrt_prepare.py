"""Failure-boundary checks; no network or firmware build is used here."""
import importlib.util
import json
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("prepare", Path(__file__).parents[1] / "boltwrt-prepare.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="boltwrt-prepare-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def repo(self):
        repo = self.root / "repo"
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.name", "Fixture")
        self.git(repo, "config", "user.email", "fixture@example.invalid")
        (repo / "Makefile").write_text("include old-path\n")
        self.git(repo, "add", "Makefile")
        self.git(repo, "commit", "-qm", "fixture")
        return repo

    def git(self, repo, *args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    def test_exact_commit_and_no_checkout_of_existing_different_head(self):
        origin = self.repo()
        sha = self.git(origin, "rev-parse", "HEAD")
        clone = self.root / "clone"
        prepare.checkout(clone, str(origin), sha)
        self.assertEqual(self.git(clone, "rev-parse", "HEAD"), sha)
        prepare.checkout(clone, str(origin), sha)
        with self.assertRaisesRegex(prepare.PrepareError, "HEAD differs"):
            prepare.checkout(clone, str(origin), "1" * 40)
        self.assertEqual(self.git(clone, "rev-parse", "HEAD"), sha)

    def test_only_complete_known_patches_can_repeat(self):
        repo = self.repo()
        patches = {"Makefile": [("include old-path", "include new-path")]}
        expected = prepare.check_worktree(repo, patches)
        (repo / "Makefile").write_bytes(expected["Makefile"])
        self.assertEqual(prepare.check_worktree(repo, patches), expected)
        (repo / "Makefile").write_text("include new-path\n# user's change\n")
        with self.assertRaisesRegex(prepare.PrepareError, "neither locked"):
            prepare.check_worktree(repo, patches)
        self.assertIn("user's change", (repo / "Makefile").read_text())

    def test_failed_fetch_does_not_poison_destination_and_can_retry(self):
        origin = self.repo()
        sha = self.git(origin, "rev-parse", "HEAD")
        held_origin = self.root / "held-origin"
        origin.rename(held_origin)
        clone = self.root / "clone"
        note = self.root / "user-note"
        note.write_text("preserve")
        with self.assertRaises(prepare.PrepareError):
            prepare.checkout(clone, str(origin), sha)
        self.assertFalse(clone.exists())
        self.assertEqual(list(self.root.glob(".clone.fetch-*")), [])
        self.assertEqual(note.read_text(), "preserve")
        held_origin.rename(origin)
        prepare.checkout(clone, str(origin), sha)
        self.assertEqual(self.git(clone, "rev-parse", "HEAD"), sha)
        unknown = self.root / "unknown"
        unknown.mkdir()
        (unknown / "note").write_text("preserve")
        with self.assertRaisesRegex(prepare.PrepareError, "non-repository"):
            prepare.checkout(unknown, str(origin), sha)
        self.assertEqual((unknown / "note").read_text(), "preserve")

    def test_untracked_and_staged_changes_are_rejected(self):
        repo = self.repo()
        (repo / "user-note").write_text("preserve me")
        with self.assertRaisesRegex(prepare.PrepareError, "untracked"):
            prepare.check_worktree(repo, {})
        self.git(repo, "add", "user-note")
        with self.assertRaisesRegex(prepare.PrepareError, "staged"):
            prepare.check_worktree(repo, {})
        self.assertEqual((repo / "user-note").read_text(), "preserve me")

    def test_locked_patch_anchor_drift_is_fatal(self):
        with self.assertRaises(prepare.PrepareError):
            prepare.exact_patch("different upstream", [("old", "new")], "fixture")
        with self.assertRaises(prepare.PrepareError):
            prepare.exact_patch("old old", [("old", "new")], "fixture")

    def test_compatibility_manifest_is_bound_to_committed_gitlink(self):
        repo = self.repo()
        manifest_path = repo / "compat.json"
        child = "net/openwrt-smartdns"
        sha = "1" * 40
        manifest = {"submodules": {child: {"commit": sha, "files": {
            "Makefile": [["VERSION:=fixed", "SOURCE_VERSION:=fixed"]]}}}}
        manifest_path.write_text(json.dumps(manifest))
        self.git(repo, "add", "compat.json")
        self.git(repo, "commit", "-qm", "compatibility fixture")
        spec = {"patch_manifest": "compat.json"}
        expected = prepare.submodule_patches(repo, spec, {child: sha})
        self.assertEqual(len(expected[child]["Makefile"]), 2)  # Existing Rust edit plus source pin.
        self.assertEqual(expected, prepare.submodule_patches(repo, spec, {child: sha}))
        manifest_path.write_text("not committed data")
        self.assertEqual(expected, prepare.submodule_patches(repo, spec, {child: sha}))
        with self.assertRaisesRegex(prepare.PrepareError, "locked gitlink"):
            prepare.submodule_patches(repo, spec, {child: "2" * 40})
        with self.assertRaises(prepare.PrepareError):
            prepare.submodule_patches(repo, {"patch_manifest": "../outside"}, {child: sha})

    def test_source_names_differ_but_binary_provider_conflict_is_detected(self):
        custom = "feeds/BoltWrt/net/openwrt-smartdns"
        official = "feeds/packages/net/smartdns"
        other = "feeds/packages/net/curl"
        winners, rejected = prepare.provider_plan({
            "packages": {official: {"smartdns"}, other: {"curl"}},
            "BoltWrt": {custom: {"smartdns", "smartdns-ui"}},
        })
        self.assertEqual(winners, {custom: "BoltWrt", other: "packages"})
        self.assertEqual(rejected, {official: "packages"})
        for source, feed in {**winners, **rejected}.items():
            target = self.root / source
            target.mkdir(parents=True)
            link = self.root / "package/feeds" / feed / Path(source).name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
        unknown = self.root / "package/feeds/packages/user-note"
        unknown.write_text("keep")
        prepare.verify_links(self.root, winners, rejected, remove_rejected=True)
        self.assertFalse((self.root / "package/feeds/packages/smartdns").is_symlink())
        self.assertEqual(unknown.read_text(), "keep")
        link = self.root / "package/feeds/BoltWrt/openwrt-smartdns"
        link.unlink()
        link.symlink_to(self.root / "wrong")
        with self.assertRaisesRegex(prepare.PrepareError, "unknown provider"):
            prepare.verify_links(self.root, winners, rejected)

    def test_hidden_child_make_failure_is_not_success(self):
        (self.root / "scripts").mkdir()
        script = self.root / "scripts/feeds"
        script.write_text('#!/bin/sh\ngmake -f /dev/null missing-target\nexit 0\n')
        script.chmod(0o755)
        env, failures = prepare.make_environment(self.root)
        with self.assertRaisesRegex(prepare.PrepareError, "make command inside"):
            prepare.feeds_command(self.root, env, failures, "update", "-i")
        self.assertIn("missing-target", failures.read_text())

    def test_unknown_build_links_and_directories_rejected_notes_preserved(self):
        tree = self.root / "package/feeds/oldfeed"
        tree.mkdir(parents=True)
        note = tree / "note"
        note.write_text("ordinary note")
        prepare.verify_links(self.root, {}, {})
        external = self.root / "external"
        external.mkdir()
        (external / "Makefile").write_text("$(eval $(call BuildPackage,evil))\n")
        evil = tree / "evil"
        evil.symlink_to(external)
        with self.assertRaisesRegex(prepare.PrepareError, "unknown build source"):
            prepare.verify_links(self.root, {}, {}, remove_rejected=True)
        self.assertTrue(evil.is_symlink())
        self.assertEqual(note.read_text(), "ordinary note")
        evil.unlink()
        evil.mkdir()
        (evil / "Makefile").write_text("$(eval $(call BuildPackage,evil))\n")
        with self.assertRaisesRegex(prepare.PrepareError, "unknown build source"):
            prepare.verify_links(self.root, {}, {})
        self.assertTrue((evil / "Makefile").is_file())

    def test_preindex_allowlist_does_not_admit_untracked_makefiles(self):
        repo = self.repo()
        target = self.root / "feeds/BoltWrt"
        target.parent.mkdir()
        repo.rename(target)
        (target / "injected").mkdir()
        (target / "injected/Makefile").write_text("$(eval $(call BuildPackage,evil))\n")
        link = self.root / "package/feeds/BoltWrt/injected"
        link.parent.mkdir(parents=True)
        link.symlink_to(target / "injected")
        allowed = prepare.locked_source_links(self.root, [(target, {})])
        with self.assertRaisesRegex(prepare.PrepareError, "unknown build source"):
            prepare.verify_package_tree(self.root, allowed)

    def test_product_roots_intersect_real_packages_from_all_targets(self):
        config = self.root / "config"
        (config / "targets").mkdir(parents=True)
        (config / "common.txt").write_text(
            "CONFIG_PACKAGE_web=y\nCONFIG_PACKAGE_web_OPTION=y\n# CONFIG_PACKAGE_weechat is not set\n")
        (config / "targets/first.txt").write_text("CONFIG_PACKAGE_monitor=m\n")
        (config / "targets/second.txt").write_text("CONFIG_PACKAGE_modem=y\n")
        indexes = {"packages": {"feeds/packages/web": {"web"},
                    "feeds/packages/weechat": {"weechat-minimal", "weechat-full"},
                    "feeds/packages/monitor": {"monitor"}, "feeds/packages/modem": {"modem"}},
                   "BoltWrt": {"feeds/BoltWrt/proxy": {"proxy"}}}
        winners, _ = prepare.provider_plan(indexes)
        roots = prepare.product_roots(indexes, winners, prepare.positive_package_choices(self.root))
        self.assertEqual(set(roots), {"feeds/packages/web", "feeds/packages/monitor",
                                     "feeds/packages/modem", "feeds/BoltWrt/proxy"})

    def test_explicit_haproxy_backend_seeds_official_provider(self):
        (self.root / "config/targets").mkdir(parents=True)
        (self.root / "config/targets/x86-64.txt").write_text("")
        common = self.root / "config/common.txt"
        common.write_text("CONFIG_PACKAGE_luci-app-passwall2_INCLUDE_Haproxy=y\n")
        indexes = {"BoltWrt": {"feeds/BoltWrt/luci-app-passwall2": {"luci-app-passwall2"}},
                   "packages": {"feeds/packages/net/haproxy": {"haproxy"},
                                "feeds/packages/net/weechat": {"weechat"}}}
        winners, _ = prepare.provider_plan(indexes)
        self.assertNotIn("feeds/packages/net/haproxy", prepare.product_roots(
            indexes, winners, prepare.positive_package_choices(self.root)))
        with common.open("a") as handle:
            handle.write("CONFIG_PACKAGE_haproxy=y\n")
        roots = prepare.product_roots(indexes, winners, prepare.positive_package_choices(self.root))
        self.assertEqual(roots, {"feeds/BoltWrt/luci-app-passwall2": "BoltWrt",
                                 "feeds/packages/net/haproxy": "packages"})

    def test_legacy_registration_preserved_and_scoped_registration_repeats(self):
        source = self.root / "feeds/packages/unused"
        source.mkdir(parents=True)
        (source / "Makefile").write_text("$(eval $(call BuildPackage,unused))\n")
        link = self.root / "package/feeds/packages/unused"
        link.parent.mkdir(parents=True)
        link.symlink_to(source)
        scope = {"mode": "product-roots-v1", "positive_package_choices": ["web"]}
        allowed = {link: {source.resolve()}}
        with self.assertRaisesRegex(prepare.PrepareError, "clean build directory"):
            prepare.check_registration_scope(self.root, scope, allowed)
        self.assertTrue(link.is_symlink())
        logs = self.root / "logs"
        logs.mkdir()
        record = {"registration_scope": scope,
                  "selected_providers": {"feeds/packages/unused": "packages"}}
        (logs / "prepare-sources.json").write_text(json.dumps(record))
        prepare.check_registration_scope(self.root, scope, allowed)
        with self.assertRaisesRegex(prepare.PrepareError, "changed product"):
            prepare.check_registration_scope(self.root, {"mode": "changed"}, allowed)
        self.assertTrue(link.is_symlink())
        extra = self.root / "package/feeds/packages/extra"
        extra.symlink_to(source)
        with self.assertRaisesRegex(prepare.PrepareError, "unknown build source"):
            prepare.check_registration_scope(self.root, scope, {**allowed, extra: {source.resolve()}})
        self.assertTrue(extra.is_symlink())

    def test_zero_exit_kconfig_error_is_rejected_from_feeds_and_make(self):
        diagnostic = "tmp/.config-package.in:7:error: recursive dependency detected!"
        (self.root / "scripts").mkdir()
        script = self.root / "scripts/feeds"
        script.write_text(f"#!/bin/sh\nprintf '%s\\n' '{diagnostic}'\nexit 0\n")
        script.chmod(0o755)
        with self.assertRaisesRegex(prepare.PrepareError, "Kconfig reported"):
            prepare.feeds_command(self.root, dict(os.environ), self.root / "failures", "update", "-i")
        (self.root / "Makefile").write_text(f"defconfig:\n\t@printf '%s\\n' '{diagnostic}'\n")
        with self.assertRaisesRegex(prepare.PrepareError, "Kconfig reported"):
            prepare.checked_make(self.root, dict(os.environ), "defconfig")
        prepare.reject_kconfig_errors('warning: ordinary warning\nsource = "recursive dependency detected!"\n')


if __name__ == "__main__":
    unittest.main()
