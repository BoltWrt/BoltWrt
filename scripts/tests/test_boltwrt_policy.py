"""Exercise retirement of router-side credential copies in a private sandbox."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CLEANUP = ROOT / 'package/boltwrt-defaults/files/etc/uci-defaults/94-boltwrt-migration-cleanup'


class BackupCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / 'etc/boltwrt'
        self.backup = self.base / 'migrations/4.0.1'
        self.backup.mkdir(parents=True)
        self.live = self.root / 'etc/config'
        self.live.mkdir()
        (self.live / 'passwall2').write_text('live credentials')
        (self.live / 'passwall2_server').write_text('live server credentials')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        logger = self.bin / 'logger'
        logger.write_text('#!/bin/sh\nexit 0\n')
        logger.chmod(0o755)

    def run_cleanup(self):
        # Substitute only the fixed namespace root; execute the real script.
        script = CLEANUP.read_text().replace('/etc/boltwrt', str(self.base))
        return subprocess.run(['sh', '-c', script], capture_output=True,
                              env=dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH']))

    def assert_live(self):
        self.assertEqual((self.live / 'passwall2').read_text(), 'live credentials')
        self.assertEqual((self.live / 'passwall2_server').read_text(), 'live server credentials')

    def test_known_files_removed_empty_namespace_removed_and_repeat_succeeds(self):
        for name in ('passwall2', 'passwall2_server'):
            (self.backup / name).write_text('old private copy')
        self.assertEqual(self.run_cleanup().returncode, 0)
        self.assertFalse(self.base.exists())
        self.assertEqual(self.run_cleanup().returncode, 0)
        self.assert_live()

    def test_unknown_files_and_exported_backup_are_preserved(self):
        (self.backup / 'passwall2').write_text('old private copy')
        (self.backup / 'user-file').write_text('keep')
        exported = self.root / 'user-backup.tar.gz'
        exported.write_bytes(b'user export')
        self.assertEqual(self.run_cleanup().returncode, 0)
        self.assertFalse((self.backup / 'passwall2').exists())
        self.assertEqual((self.backup / 'user-file').read_text(), 'keep')
        self.assertEqual(exported.read_bytes(), b'user export')
        self.assert_live()

    def test_leaf_symlink_unlinked_without_touching_target(self):
        (self.backup / 'passwall2').symlink_to(self.live / 'passwall2')
        self.assertEqual(self.run_cleanup().returncode, 0)
        self.assertFalse((self.backup / 'passwall2').is_symlink())
        self.assert_live()

    def test_each_parent_symlink_refused_without_deleting_live_files(self):
        for relative in ('migrations/4.0.1', 'migrations', '.'):
            with self.subTest(parent=relative):
                target = self.base / relative
                saved = self.root / ('saved-' + relative.replace('/', '-'))
                target.rename(saved)
                target.symlink_to(self.live, target_is_directory=True)
                self.assertNotEqual(self.run_cleanup().returncode, 0)
                self.assert_live()
                target.unlink()
                saved.rename(target)


if __name__ == '__main__':
    unittest.main()
