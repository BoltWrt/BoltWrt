"""A failure in any download phase must reach the top-level make caller."""
from pathlib import Path
import hashlib
import subprocess
import sys
import tempfile
import unittest


class DownloadStatusTests(unittest.TestCase):
    def test_actual_download_recipe_stops_before_later_success(self):
        lines = (Path(__file__).parents[2] / "include/toplevel.mk").read_text().splitlines()
        position = next(i for i, line in enumerate(lines) if line.startswith("download: .config"))
        recipe = lines[position + 1]
        self.assertTrue(recipe.startswith("\t"))
        with tempfile.TemporaryDirectory(prefix="boltwrt-download-status-") as name:
            root = Path(name)
            phase = root / "phase"
            phase.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> visited\n'
                             '[ "$1" != "package/download" ]\n')
            phase.chmod(0o755)
            (root / "Makefile").write_text(
                "DOWNLOAD_DIRS := tools/download toolchain/download package/download target/download\n"
                "SUBMAKE := ./phase\n.PHONY: download\ndownload:\n" + recipe + "\n")
            result = subprocess.run(["make", "download"], cwd=root, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual((root / "visited").read_text().splitlines(),
                             ["tools/download", "toolchain/download", "package/download"])


class DownloadCacheTests(unittest.TestCase):
    def check_cache(self, downloads, cached, repeat_guard=False):
        """Run the real Download macro; a forced recipe deliberately fails."""
        with tempfile.TemporaryDirectory(prefix="boltwrt-download-cache-") as name:
            root = Path(name)
            dl = root / "dl"
            dl.mkdir()
            for filename, data in cached.items():
                (dl / filename).write_bytes(data)
            mkhash = root / "mkhash"
            mkhash.write_text(
                "#!" + sys.executable + "\nimport hashlib, pathlib, sys\n"
                "assert sys.argv[1] == 'sha256'\n"
                "with open(pathlib.Path(__file__).parent / 'hash-calls', 'a') as log:\n"
                " log.write(sys.argv[2] + '\\n')\n"
                "print(hashlib.sha256(pathlib.Path(sys.argv[2]).read_bytes()).hexdigest())\n")
            mkhash.chmod(0o755)
            reject = root / "reject-cache"
            reject.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> visited\nexit 17\n')
            reject.chmod(0o755)
            actual = str(Path(__file__).parents[2] / "include/download.mk").replace(" ", "\\ ")
            fixture = (".DEFAULT_GOAL := download\n.PHONY: download FORCE\nFORCE:\n"
                       f"DL_DIR := {dl}\nMKHASH := {mkhash}\ninclude {actual}\n"
                       "locked = $(1)\n"
                       "DownloadMethod/default = ./reject-cache $(FILE)\n"
                       "DownloadMethod/git = ./reject-cache $(FILE)\n")
            for index, download in enumerate(downloads):
                fixture += f"define Download/cache{index}\n"
                fixture += "URL := https://example.invalid/source\n"
                fixture += "\n".join(f"{key} := {value}" for key, value in download.items())
                fixture += f"\nendef\n$(eval $(call Download,cache{index}))\n"
            if repeat_guard:
                # BuildPackage/HostBuild retain this second call for the primary source.
                fixture += "$(eval $(call check_download_integrity))\n"
            (root / "Makefile").write_text(fixture)
            result = subprocess.run(["make", "download"], cwd=root, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            visited = (root / "visited").read_text().splitlines() if (root / "visited").exists() else []
            hashes = (root / "hash-calls").read_text().splitlines()
            return result, visited, hashes

    def test_correct_named_http_cache_is_reused_and_hash_takes_priority(self):
        data = b"locked source"
        result, visited, hashes = self.check_cache(
            [{"FILE": "webui.tar.gz", "HASH": hashlib.sha256(data).hexdigest(),
              "MIRROR_HASH": "0" * 64}], {"webui.tar.gz": data})
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(visited, [])
        self.assertEqual(len(hashes), 1)

    def test_corrupt_named_http_cache_cannot_silently_pass(self):
        result, visited, hashes = self.check_cache(
            [{"FILE": "webui.tar.gz", "HASH": hashlib.sha256(b"locked source").hexdigest()}],
            {"webui.tar.gz": b"wrong cached source"}, repeat_guard=True)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(visited, ["webui.tar.gz"])
        self.assertEqual(len(hashes), 1)

    def test_named_git_cache_uses_mirror_hash_when_hash_is_empty_or_x(self):
        for value in ("", "x"):
            with self.subTest(hash=value):
                result, visited, hashes = self.check_cache(
                    [{"FILE": "webui.tar.gz", "PROTO": "git", "SUBDIR": "webui",
                      "SOURCE_VERSION": "1" * 40, "HASH": value,
                      "MIRROR_HASH": hashlib.sha256(b"locked source").hexdigest()}],
                    {"webui.tar.gz": b"wrong cached source"})
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(visited, ["webui.tar.gz"])
                self.assertEqual(len(hashes), 1)

    def test_named_downloads_keep_independent_expected_hashes(self):
        result, visited, hashes = self.check_cache(
            [{"FILE": "first.tar.gz", "HASH": hashlib.sha256(b"first").hexdigest()},
             {"FILE": "second.tar.gz", "HASH": hashlib.sha256(b"second").hexdigest()}],
            {"first.tar.gz": b"first", "second.tar.gz": b"wrong second source"})
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(visited, ["second.tar.gz"])
        self.assertEqual(len(hashes), 2)


if __name__ == "__main__":
    unittest.main()
