"""Run only in a disposable Linux container with the baseline's native UCI/jshn.

No router, provider API or production credentials are used.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

if not Path("/.dockerenv").is_file():
    raise SystemExit("Run only inside a disposable Docker container")


ROOT = Path('/work')
FILES = ROOT / 'package/boltwrt-defaults/files/etc'


def run(*args, check=True, env=None):
    return subprocess.run(args, check=check, text=True, capture_output=True, env=env)


def get(key):
    return run('uci', '-q', 'get', key).stdout.strip()


class DefaultsTest(unittest.TestCase):
    def setUp(self):
        for path in Path('/etc/config').glob('*'):
            path.unlink()
        for path in Path('/tmp/.uci').glob('*'):
            if path.is_file():
                path.unlink()

    def board(self):
        path = Path('/tmp/boltwrt-board.json')
        path.write_text(json.dumps({'network': {'lan': {'device': 'eth0', 'protocol': 'static'}},
                                    'system': {}}))
        env = dict(os.environ, CFG=str(path))
        script = str(FILES / 'board.d/99-boltwrt')
        run('bash', script, env=env)
        first = json.loads(path.read_text())
        run('bash', script, env=env)
        self.assertEqual(first, json.loads(path.read_text()))
        Path('/etc/board.json').write_text(path.read_text())
        return first

    def generate(self):
        run('bash', str(ROOT / 'package/base-files/files/bin/config_generate'))

    def test_fresh_install_and_repeated_generation(self):
        board = self.board()
        self.assertEqual(board['network']['lan']['device'], 'eth0')
        self.generate()
        self.assertEqual(get('network.lan.ipaddr'), '10.126.0.1/18')
        self.assertEqual(get('system.@system[0].hostname'), 'BoltWrt')
        self.assertEqual(get('system.ntp.server').split(),
                         ['ntp.aliyun.com', 'time.nist.gov', 'ntp.ubuntu.com', 'time.windows.com'])
        first = {p.name: p.read_bytes() for p in Path('/etc/config').glob('*')}
        self.generate()
        self.assertEqual(first, {p.name: p.read_bytes() for p in Path('/etc/config').glob('*')})

    def test_upgrade_keeps_user_network_and_system(self):
        self.board()
        for filename, content in {
            'network': "config interface 'lan'\n option proto 'static'\n option ipaddr '192.168.1.1'\n option netmask '255.255.255.0'\n",
            'system': "config system\n option hostname 'UserRouter'\nconfig timeserver 'ntp'\n list server 'user.example'\n",
        }.items():
            Path('/etc/config', filename).write_text(content)
        before = {p.name: p.read_bytes() for p in Path('/etc/config').glob('*')}
        self.generate()
        self.assertEqual(before, {p.name: p.read_bytes() for p in Path('/etc/config').glob('*')})

    def service(self, name, domain, provider='aliyun.com', anonymous=False):
        with Path('/etc/config/ddns').open('a') as f:
            section = '' if anonymous else repr(name)
            f.write(f"config service {section}\n option service_name '{provider}'\n option domain '{domain}'\n"
                    " option username 'fixture-access-key'\n option password 'fixture-secret'\n option enabled '1'\n")

    def migrate(self, check=True, env=None):
        return run('bash', str(FILES / 'uci-defaults/95-boltwrt-aliyun'), check=check, env=env)

    def test_second_set_failure_is_isolated_from_boot_commit(self):
        self.service('first', 'home.example.com')
        self.service('second', 'v6.example.com')
        Path('/etc/config/system').write_text("config system 'main'\n option hostname 'original'\n")
        run('uci', 'set', 'ddns.first.enabled=0')
        run('uci', 'set', 'system.main.hostname=PendingUserName')
        before = Path('/etc/config/ddns').read_bytes()
        deltas = {p.name: p.read_bytes() for p in Path('/tmp/.uci').glob('*')}
        private_before = set(Path('/tmp').glob('boltwrt-aliyun.*'))
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            # Delegate every operation to native UCI. Fail the second set via a
            # genuine missing-section lookup after the first delta was saved.
            wrapper = directory / 'uci'
            wrapper.write_text('''#!/bin/bash
for arg in "$@"; do
    if [ "$arg" = set ]; then
        if [ -f "$TEST_UCI_DIR/first-staged" ]; then
            exec /usr/bin/uci -q -t "$savedir" set ddns.missing.domain=failure
        fi
        /usr/bin/uci "$@" || exit $?
        [ -s "$savedir/ddns" ] || exit 99
        touch "$TEST_UCI_DIR/first-staged"
        exit 0
    fi
    if [ "$previous" = -t ]; then savedir="$arg"; fi
    previous="$arg"
done
exec /usr/bin/uci "$@"
''')
            wrapper.chmod(0o755)
            logger = directory / 'logger'
            logger.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_UCI_DIR/log"\n')
            logger.chmod(0o755)
            env = dict(os.environ, TEST_UCI_DIR=tmp, PATH=f'{tmp}:' + os.environ['PATH'])
            result = self.migrate(check=False, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((directory / 'first-staged').exists())
            messages = result.stdout + result.stderr + (directory / 'log').read_text()
            self.assertIn('migration will retry', messages)
            for secret in ['fixture-', 'home.example.com', 'v6.example.com', 'home@example.com']:
                self.assertNotIn(secret, messages)
        self.assertEqual(before, Path('/etc/config/ddns').read_bytes())
        self.assertEqual(deltas, {p.name: p.read_bytes() for p in Path('/tmp/.uci').glob('*')})
        self.assertEqual(private_before, set(Path('/tmp').glob('boltwrt-aliyun.*')))
        # boot runs this even when the defaults script exits nonzero.
        run('uci', 'commit')
        self.assertEqual(get('ddns.first.domain'), 'home.example.com')
        self.assertEqual(get('ddns.second.domain'), 'v6.example.com')
        self.assertEqual(get('ddns.first.enabled'), '0')
        self.assertEqual(get('system.main.hostname'), 'PendingUserName')
        self.migrate()
        self.assertEqual(get('ddns.first.domain'), 'home@example.com')
        self.assertEqual(get('ddns.second.domain'), 'v6@example.com')
        migrated = Path('/etc/config/ddns').read_bytes()
        self.migrate()
        self.assertEqual(migrated, Path('/etc/config/ddns').read_bytes())

    def test_legacy_conversion_and_credentials_preserved(self):
        self.service('host', 'home.example.com')
        self.service('root', '@.example.com')
        self.service('new', 'www@example.com')
        self.service('other', 'home.example.com', 'cloudflare.com-v4')
        self.service('anonymous', 'v6.example.com', anonymous=True)
        self.migrate()
        self.assertEqual(get('ddns.host.domain'), 'home@example.com')
        self.assertEqual(get('ddns.root.domain'), '@example.com')
        self.assertEqual(get('ddns.new.domain'), 'www@example.com')
        self.assertEqual(get('ddns.other.domain'), 'home.example.com')
        self.assertEqual(get('ddns.@service[-1].domain'), 'v6@example.com')
        for section in ['host', 'root', 'new', 'other', '@service[-1]']:
            self.assertTrue(get(f'ddns.{section}.username') == 'fixture-access-key')
            self.assertTrue(get(f'ddns.{section}.password') == 'fixture-secret')
            self.assertEqual(get(f'ddns.{section}.enabled'), '1')
        before = Path('/etc/config/ddns').read_bytes()
        self.migrate()
        self.assertEqual(before, Path('/etc/config/ddns').read_bytes())

    def test_invalid_legacy_input_does_not_partially_convert(self):
        self.service('valid', 'home.example.com')
        self.service('invalid', 'localhost')
        before = Path('/etc/config/ddns').read_bytes()
        result = self.migrate(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, Path('/etc/config/ddns').read_bytes())
        self.assertEqual(run('uci', '-q', 'changes', 'ddns').stdout, '')
        self.assertNotIn('fixture-', result.stdout + result.stderr)

    def test_absent_ddns_is_noop(self):
        self.migrate()
        self.assertFalse(Path('/etc/config/ddns').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
