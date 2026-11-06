"""Native ash + isolated fake init; never invokes a target binary or real service."""
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

if sys.flags.optimize or not Path('/.dockerenv').is_file():
    raise SystemExit('Run unoptimized Python only inside a disposable Docker container')
SHELL = Path(os.environ['BOLTWRT_TEST_SHELL'])
if not SHELL.is_absolute() or not os.access(SHELL, os.X_OK):
    raise SystemExit('BOLTWRT_TEST_SHELL must be an executable absolute path')
SOURCE = Path(__file__).parents[1] / 'files/etc/uci-defaults/09-boltwrt-haproxy'
print('native shell sha256=' + hashlib.sha256(SHELL.read_bytes()).hexdigest(), flush=True)


class HaproxyDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='haproxy-defaults-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('etc/uci-defaults', 'etc/init.d', 'etc/rc.d'):
            (self.root / directory).mkdir(parents=True)
        self.marker = self.root / 'etc/uci-defaults/10_disable_services'
        self.init = self.root / 'etc/init.d/haproxy'
        self.log = self.root / 'calls'
        self.cfg = self.root / 'etc/haproxy.cfg'
        self.cfg.write_text('user configuration remains byte-identical\n')
        original = SOURCE.read_text()
        # Only absolute filesystem prefixes are adapted, with no injected product
        # test knobs. Assert all product /etc references are inside this fixture.
        for prefix in ('/etc/uci-defaults/', '/etc/init.d/', '/etc/rc.d/'):
            original = original.replace(prefix, str(self.root) + prefix)
        self.assertNotIn(' /etc/', original)
        self.script = self.root / 'hook'
        self.script.write_text(original)

    def fake_init(self, mode='ok'):
        code = f'#!{SHELL}\nprintf "%s\\n" "$@" >> {shlex.quote(str(self.log))}\n'
        if mode == 'fail':
            code += 'exit 7\n'
        elif mode == 'false-success':
            code += 'exit 0\n'
        else:
            code += f'[ "$#" -eq 1 ] && [ "$1" = disable ] || exit 9\nrm -f {shlex.quote(str(self.root))}/etc/rc.d/S??haproxy {shlex.quote(str(self.root))}/etc/rc.d/K??haproxy\n'
        self.init.write_text(code)
        self.init.chmod(0o755)
        for name in ('S99haproxy', 'K80haproxy'):
            p = self.root / 'etc/rc.d' / name
            if not p.is_symlink():
                p.symlink_to('../init.d/haproxy')

    def run_hook(self):
        result = subprocess.run([str(SHELL), str(self.script)], text=True, capture_output=True)
        self.assertEqual(self.cfg.read_text(), 'user configuration remains byte-identical\n')
        return result

    def test_fresh_disables_only_independent_init_and_repeats(self):
        self.fake_init()
        unrelated = self.root / 'etc/rc.d/S80nginx'
        unrelated.symlink_to('../init.d/nginx')
        for _ in range(2):
            self.assertEqual(self.run_hook().returncode, 0)
        self.assertEqual(self.log.read_text(), 'disable\ndisable\n')
        self.assertFalse((self.root / 'etc/rc.d/S99haproxy').is_symlink())
        self.assertTrue(unrelated.is_symlink())

    def test_standard_upgrade_enabled_keeps_image_links(self):
        self.fake_init()
        self.marker.write_text('\nexit 0\n')  # Actual empty disabled-list template.
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertTrue((self.root / 'etc/rc.d/S99haproxy').is_symlink())

    def test_standard_upgrade_disabled_is_left_for_following_10(self):
        self.fake_init()
        self.marker.write_text(shlex.quote(str(self.init)) + ' disable\n\nexit 0\n')
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertFalse(self.log.exists())
        subprocess.run([str(SHELL), str(self.marker)], check=True)
        self.assertEqual(self.log.read_text(), 'disable\n')
        self.assertFalse((self.root / 'etc/rc.d/S99haproxy').is_symlink())

    def test_minimal_recovery_without_init_skips(self):
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertFalse(self.log.exists())

    def test_failure_is_visible_and_can_retry(self):
        self.fake_init('fail')
        failed = self.run_hook()
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('failed to disable', failed.stderr)
        self.assertTrue((self.root / 'etc/rc.d/S99haproxy').is_symlink())
        self.fake_init()
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertFalse((self.root / 'etc/rc.d/S99haproxy').is_symlink())

    def test_success_return_with_links_left_is_rejected(self):
        self.fake_init('false-success')
        result = self.run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('links remain', result.stderr)

    def test_nonregular_restored_marker_fails_closed(self):
        self.fake_init()
        self.marker.symlink_to(self.cfg)
        result = self.run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid restored service-state marker', result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
