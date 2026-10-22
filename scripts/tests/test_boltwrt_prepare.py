"""Failure-boundary checks; no network or firmware build is used here."""
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
