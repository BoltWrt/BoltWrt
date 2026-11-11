"""Persistent, isolated QEMU preview of an existing BoltWrt release."""

import argparse
import fcntl
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import signal
import socket
import ssl
import subprocess
import tarfile
import time
from types import SimpleNamespace

DATA = Path('/data')
VM = DATA / 'vm'
THEME = Path('/theme')
ASSET_SHA256 = '9cb99f69be67bc3f517d4c51fecfc547a4f142298d58a748ec7814f6490432b3'
ASSET_BYTES = 570335990
ASSET_URL = 'https://github.com/BoltWrt/BoltWrt/releases/download/v4.0.0/BoltWrt_x86-64_v4.0.0.tar.gz'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(name, value):
    target = DATA / name
    temporary = target.with_suffix(target.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(target)


def read(name, default=None):
    path = DATA / name
    return json.loads(path.read_text()) if path.exists() else default


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, **kwargs)


def prepare():
    if (DATA / 'prepared.json').exists() or (DATA / 'base.vmdk').exists():
        raise RuntimeError('Firmware is already imported; existing disks are preserved.')
    source = Path('/input/firmware.tar.gz')
    if source.stat().st_size != ASSET_BYTES or digest(source) != ASSET_SHA256:
        raise ValueError('Firmware does not match the official BoltWrt 4.0.0 x86-64 asset.')
    with tarfile.open(source, 'r:gz') as archive:
        matches = [m for m in archive.getmembers() if m.name.endswith('-combined.vmdk')]
        if len(matches) != 1:
            raise ValueError('Expected exactly one BIOS combined VMDK.')
        member = matches[0]
        path = PurePosixPath(member.name)
        if not member.isfile() or path.is_absolute() or '..' in path.parts or member.size > 4 * 1024**3:
            raise ValueError('Unexpected firmware archive entry.')
        temporary = DATA / 'base.vmdk.part'
        with archive.extractfile(member) as incoming, temporary.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        temporary.chmod(0o444)
        info = json.loads(run(['qemu-img', 'info', '--output=json', str(temporary)]).stdout)
        if info['format'] != 'vmdk' or 'backing-filename' in info:
            raise ValueError('Firmware must be a self-contained VMDK.')
        temporary.replace(DATA / 'base.vmdk')
    save('prepared.json', {'asset_url': ASSET_URL, 'asset_sha256': ASSET_SHA256,
                           'member': member.name, 'base_sha256': digest(DATA / 'base.vmdk'),
                           'base_bytes': member.size, 'disk_info': info})
    print('Official firmware verified and imported. Original download is unchanged.', flush=True)


def ssh(command, payload=None, timeout=90):
    arguments = ['ssh', '-F', '/dev/null', '-i', str(DATA / 'id_ed25519'),
                 '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                 '-o', 'StrictHostKeyChecking=accept-new',
                 '-o', 'UserKnownHostsFile=' + str(DATA / 'known_hosts'),
                 '-o', 'ConnectTimeout=5', '-o', 'LogLevel=ERROR',
                 '-p', '2223', 'root@127.0.0.1', command]
    return run(arguments, input=payload, timeout=timeout).stdout


def source_files():
    files = {}
    for directory, prefix in [('htdocs', 'www'), ('ucode', 'usr/share/ucode/luci')]:
        for path in sorted((THEME / directory).rglob('*')):
            if path.is_symlink():
                raise ValueError('Theme source symlinks are not supported: ' + str(path))
            if path.is_file() and not any(p.startswith('.') for p in path.relative_to(THEME).parts):
                files[str(PurePosixPath(prefix) / path.relative_to(THEME / directory).as_posix())] = path.read_bytes()
    if not files:
        raise ValueError('Theme source is empty.')
    return files


def wait_for_web(timeout=120):
    certificate_path = ssh('uci -q get nginx._lan.ssl_certificate').decode().strip()
    if not certificate_path.startswith('/etc/'):
        raise ValueError('Unexpected preview certificate path.')
    certificate = ssh('cat ' + shlex.quote(certificate_path)).decode()
    # Trust this guest's exact certificate fetched over SSH, without changing
    # the host trust store. Its router hostname differs from our forwarded IP.
    context = ssl.create_default_context(cadata=certificate)
    context.check_hostname = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        connection = http.client.HTTPSConnection('127.0.0.1', 8444, context=context, timeout=5)
        try:
            connection.request('GET', '/cgi-bin/luci/')
            response = connection.getresponse()
            response.read()
            if response.status in (200, 301, 302, 303, 403):
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(1)
    raise TimeoutError('LuCI did not become ready after restarting uWSGI.')


def sync():
    with (DATA / 'sync.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        files = source_files()
        hashes = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
        previous = read('last-sync.json', {})
        if previous.get('files') == hashes:
            return False
        revision = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:12]
        makefile = (THEME / 'Makefile').read_text()
        version = re.search(r'^PKG_VERSION\s*:?=\s*(\S+)', makefile, re.M).group(1)
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode='w:gz') as archive:
            for name, content in files.items():
                if name.endswith('.ut'):
                    text = content.decode()
                    text = re.sub(r'{#\s*([^\s]*)PKG_VERSION\s*#}', lambda m: m[1] + version, text)
                    # A changed CSS resource also changes the URL in the rendered page.
                    text = re.sub(r'(\{\{\s*(?:media|resource)\s*\}\}[^"\s]*\.(?:css|js))(?:\?v=[^"\s{]*)?(?=")',
                                  lambda m: m[1] + '?v=preview-' + revision, text)
                    content = text.encode()
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode, entry.mtime = len(content), 0o644, int(time.time())
                archive.addfile(entry, io.BytesIO(content))
        removed = sorted(set(previous.get('files', {})) - set(hashes))
        allowed = ('www/luci-static/argon/', 'www/luci-static/resources/',
                   'usr/share/ucode/luci/template/themes/argon/')
        if any(not name.startswith(allowed) or '..' in PurePosixPath(name).parts for name in removed):
            raise ValueError('Refusing an unexpected deletion in the preview guest.')
        command = 'set -e; tar -xzf - -C /; '
        if removed:
            command += 'rm -f -- ' + ' '.join(shlex.quote('/' + name) for name in removed) + '; '
        command += '/etc/init.d/uwsgi restart >/dev/null 2>&1'
        ssh(command, payload.getvalue(), timeout=120)
        wait_for_web()
        save('last-sync.json', {'revision': revision, 'time': int(time.time()), 'files': hashes})
        print('Theme synchronized: ' + revision + '. Refresh the browser.', flush=True)
        return True


def bootstrap():
    if not (DATA / 'id_ed25519').exists():
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(DATA / 'id_ed25519')])
    if not (DATA / 'credentials.json').exists():
        password = secrets.token_urlsafe(18)
        save('credentials.json', {'username': 'root', 'password': password})
        (DATA / 'password').write_text(password + '\n')
        (DATA / 'password').chmod(0o600)
    public_key = (DATA / 'id_ed25519.pub').read_text().strip()
    # Only public setup commands go over the console: stock firmware may not
    # provide stty. Password initialization happens over authenticated SSH.
    script = '\n'.join([
        'umask 077', 'mkdir -p /etc/dropbear',
        'grep -qxF ' + shlex.quote(public_key) + ' /etc/dropbear/authorized_keys 2>/dev/null || printf "%s\\n" '
        + shlex.quote(public_key) + ' >> /etc/dropbear/authorized_keys',
    ])
    spec = importlib.util.spec_from_file_location('fixture', '/runtime-tools/qemu_runtime.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    args = SimpleNamespace(case=VM, command=script, command_file=None,
                           password_file=None, timeout=600, public_input=True,
                           label='preview-public-key-setup')
    result = fixture.execute_serial(args, bytearray())
    save('public-bootstrap.json', result)
    if result['returncode']:
        raise RuntimeError('Guest bootstrap failed; see VM serial log.')


def qmp(action):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(5)
        connection.connect(str(VM / 'qmp.sock'))
        stream = connection.makefile('rwb')
        json.loads(stream.readline())
        for request in ['qmp_capabilities', action]:
            stream.write(json.dumps({'execute': request}).encode() + b'\n')
            stream.flush()
            while True:
                response = json.loads(stream.readline())
                if 'error' in response:
                    raise RuntimeError(str(response['error']))
                if 'return' in response:
                    break


def serve():
    if not (DATA / 'prepared.json').is_file():
        raise RuntimeError('Import firmware first.')
    VM.mkdir(exist_ok=True)
    overlay = VM / 'overlay.qcow2'
    if not overlay.exists():
        run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'vmdk', '-b', '/data/base.vmdk', str(overlay)])
    for name in ['serial.sock', 'qmp.sock', 'qemu.pid']:
        (VM / name).unlink(missing_ok=True)
    command = ['qemu-system-x86_64', '-accel', 'tcg', '-machine', 'pc', '-cpu', 'qemu64',
               '-smp', '1', '-m', '768', '-display', 'none', '-monitor', 'none',
               '-drive', 'file=/data/vm/overlay.qcow2,format=qcow2,if=ide',
               '-pidfile', '/data/vm/qemu.pid', '-qmp', 'unix:/data/vm/qmp.sock,server=on,wait=off',
               '-chardev', 'socket,id=console,path=/data/vm/serial.sock,server=on,wait=off,logfile=/data/vm/serial.log',
               '-serial', 'chardev:console', '-netdev',
               'user,id=lan,net=10.126.0.0/18,host=10.126.0.2,dns=10.126.0.3,dhcpstart=10.126.0.10,restrict=on,'
               'hostfwd=tcp:127.0.0.1:2223-10.126.0.1:22,hostfwd=tcp:127.0.0.1:8444-10.126.0.1:443',
               '-device', 'e1000,netdev=lan']
    machine = subprocess.Popen(command)
    relay = subprocess.Popen(['python', '-B', '/runtime-tools/tcp_forward.py', '--forward', '8443:8444'])
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True
        try:
            qmp('system_powerdown')
        except (OSError, RuntimeError):
            pass

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    save('state.json', {'status': 'booting'})
    try:
        deadline = time.monotonic() + 30
        while not (VM / 'serial.sock').exists() or not (VM / 'qemu.pid').exists():
            if machine.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('QEMU did not create its control sockets.')
            time.sleep(0.2)
        if not (DATA / 'bootstrapped.json').exists():
            bootstrap()
        deadline = time.monotonic() + 600
        while True:
            if machine.poll() is not None or relay.poll() is not None or stopping:
                raise RuntimeError('Preview process exited during startup.')
            try:
                ssh('set -e; test "$(uci -q get luci.main.mediaurlbase)" = /luci-static/argon; '
                    'ubus call system board >/dev/null', timeout=10)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Preview guest SSH did not become ready.')
                time.sleep(2)
        if not (DATA / 'bootstrapped.json').exists():
            password = read('credentials.json')['password']
            ssh('set -e; if [ ! -f /etc/boltwrt-theme-preview ]; then '
                'passwd root >/dev/null 2>&1; '
                'uci set system.@system[0].hostname=BoltWrt-Preview; uci commit system; '
                'printf "%s\\n" BoltWrt-Preview > /proc/sys/kernel/hostname; '
                'touch /etc/boltwrt-theme-preview; fi',
                (password + '\n' + password + '\n').encode())
            save('bootstrapped.json', {'time': int(time.time())})
        if not sync():
            wait_for_web()
        port = os.environ.get('BOLTWRT_PREVIEW_PORT', '18443')
        save('state.json', {'status': 'ready', 'url': 'https://127.0.0.1:' + port,
                            'credentials': '.theme-preview/credentials.json', 'watch': True})
        print('Preview ready; source changes are synchronized automatically.', flush=True)
        observed = None
        settled = None
        while not stopping and machine.poll() is None and relay.poll() is None:
            snapshot = [(str(p), p.stat().st_mtime_ns, p.stat().st_size)
                        for root in ['htdocs', 'ucode'] for p in sorted((THEME / root).rglob('*')) if p.is_file()]
            if snapshot == observed and snapshot != settled:
                try:
                    sync()
                    settled = snapshot
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    print('Theme synchronization failed: ' + type(error).__name__, flush=True)
            observed = snapshot
            time.sleep(1)
        if not stopping:
            raise RuntimeError('Preview process exited unexpectedly.')
    finally:
        if machine.poll() is None:
            try:
                qmp('system_powerdown')
                machine.wait(timeout=25)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                machine.terminate()
                machine.wait(timeout=5)
        relay.terminate()
        relay.wait(timeout=5)
        save('state.json', {'status': 'stopped'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'serve', 'sync', 'status'])
    action = parser.parse_args().action
    os.umask(0o077)
    if action == 'prepare':
        prepare()
    elif action == 'serve':
        try:
            serve()
        except Exception as error:
            failure = {'status': 'error', 'error_type': type(error).__name__,
                       'error': str(error)}
            save('last-error.json', failure)
            save('state.json', failure)
            raise
    elif action == 'sync':
        if not sync():
            print('Theme is already synchronized.')
    else:
        print(json.dumps(read('state.json', {'status': 'not started'}), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
