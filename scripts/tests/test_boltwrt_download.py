"""A failure in any download phase must reach the top-level make caller."""
from pathlib import Path
import subprocess
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


if __name__ == "__main__":
    unittest.main()
