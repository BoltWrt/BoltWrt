"""Run only in a disposable Linux container with the baseline's native UCI/jshn.

No router, provider API or production credentials are used.
"""
import json
import os
from pathlib import Path
import subprocess
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

    def migrate(self, check=True):
        return run('bash', str(FILES / 'uci-defaults/95-boltwrt-aliyun'), check=check)

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
