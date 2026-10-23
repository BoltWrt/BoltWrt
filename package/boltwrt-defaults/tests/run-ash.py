#!/usr/bin/python3
"""Offline native ash/UCI test in a new disposable container, never the builder.

Required mounts: RAX /work read-only, this tests directory /review read-only,
empty dedicated output directory /out read-write and executable (tmpfs defaults
to noexec in Docker). Use --tmpfs /out:rw,exec,size=200m,nodev,nosuid,mode=700.
Use the builder's immutable
image ID, --pull=never --network=none --log-driver=none --cpuset-cpus=2,
--user=0. No downloads, dependency installation or writes to /work.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

if sys.flags.optimize:
    raise SystemExit('Python optimization must be disabled: safety checks use assert')
assert Path('/.dockerenv').is_file() and os.getuid() == 0
assert os.environ.get('BOLTWRT_ASH_TASK') == 'isolated', 'not an active builder'
mounts = [line.split() for line in Path('/proc/mounts').read_text().splitlines()]
assert any(m[1] == '/work' and 'ro' in m[3].split(',') for m in mounts)
assert any(m[1] == '/out' and m[2] == 'tmpfs' and
           {'nodev', 'nosuid'}.issubset(m[3].split(',')) and
           'noexec' not in m[3].split(',') for m in mounts)
base = Path('/out')
assert os.statvfs(base).f_blocks * os.statvfs(base).f_frsize == 200 * 1024 ** 2
assert not list(base.iterdir()), '/out must be empty and dedicated to this task'
work = Path('/work')
host = work / 'staging_dir/host'
env = dict(os.environ, PATH=str(host / 'bin') + ':/usr/bin:/bin', LC_ALL='C',
           MAKEFLAGS='', CFLAGS='', CPPFLAGS='', LDFLAGS='')
env.pop('CROSS_COMPILE', None)
env.pop('PYTHONOPTIMIZE', None)
log = (base / 'runner.log').open('w', buffering=1)
peak_sampled_bytes = 0


def budget():
    global peak_sampled_bytes
    size = int(subprocess.check_output(['du', '-sk', str(base)]).split()[0]) * 1024
    peak_sampled_bytes = max(peak_sampled_bytes, size)
    free = os.statvfs(work).f_bavail * os.statvfs(work).f_frsize
    # Sampling is an early stop, not a guarantee of observing instantaneous
    # peak use. The independent /out tmpfs enforces the hard 200MiB ceiling.
    assert size < 175 * 1024 ** 2, f'private directory reached 175MiB guard: {size}'
    assert free >= 1024 ** 3, f'shared free space below 1GiB: {free}'
    return size


def run(*command, cwd=None):
    print('+ ' + ' '.join(map(str, command)), file=log)
    process = subprocess.Popen(list(map(str, command)), cwd=cwd, env=env,
                               stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        while process.poll() is None:
            budget()
            time.sleep(1)
        assert process.returncode == 0, f'{command[0]} exited {process.returncode}; see runner.log'
        budget()
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise


def source(recipe, archive, hash_key):
    text = recipe.read_text()
    expected = re.search(r'^' + hash_key + r':=([0-9a-f]{64})$', text, re.M).group(1)
    with archive.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    assert actual == expected, f'cached source hash mismatch: {archive}'
    print(json.dumps({'recipe': str(recipe), 'archive': str(archive), 'sha256': actual}), file=log)
    run('tar', '-xf', archive, '-C', base)


budget()
run('/usr/bin/gcc', '--version')
source(work / 'package/utils/busybox/Makefile', work / 'dl/busybox-1.37.0.tar.bz2', 'PKG_HASH')
busybox = base / 'busybox-1.37.0'
patch_hashes = {}
for patch in sorted((work / 'package/utils/busybox/patches').glob('*.patch')):
    patch_hashes[patch.name] = hashlib.sha256(patch.read_bytes()).hexdigest()
    run('patch', '--batch', '--forward', '-p1', '-i', patch, cwd=busybox)
# Only ash and current target shell semantic options, not the utility collection.
symbols = {}
for line in (work / '.config').read_text().splitlines():
    match = re.match(r'^(?:# )?CONFIG_BUSYBOX_DEFAULT_((?:ASH\w*|SHELL_ASH|SH_IS_ASH|FEATURE_SH_\w*))(=y| is not set)$', line)
    if match:
        symbols[match[1]] = match[2] == '=y'
assert symbols.get('ASH') and symbols.get('SHELL_ASH')
seed = base / 'ash.config'
seed.write_text(''.join(f'CONFIG_{key}=y\n' if value else f'# CONFIG_{key} is not set\n'
                        for key, value in symbols.items()))
run('make', '-j1', 'allnoconfig', cwd=busybox)
# BusyBox 1.37 conf.c intentionally resets boolean symbols during allnoconfig,
# even those in KCONFIG_ALLCONFIG. Apply the explicit shell options afterwards
# to the complete all-no baseline, then let oldconfig resolve dependencies.
config_path = busybox / '.config'
baseline = config_path.read_text()
for key, value in symbols.items():
    pattern = r'^(?:CONFIG_' + re.escape(key) + r'=.*|# CONFIG_' + re.escape(key) + r' is not set)$'
    replacement = f'CONFIG_{key}=y' if value else f'# CONFIG_{key} is not set'
    baseline, count = re.subn(pattern, lambda _: replacement, baseline, flags=re.M)
    assert count == 1, f'expected one BusyBox config entry: {key}'
config_path.write_text(baseline)
run('make', '-j1', 'oldconfig', cwd=busybox)
config = (busybox / '.config').read_text()
for key, value in symbols.items():
    assert (f'CONFIG_{key}=y' in config.splitlines()) == value, f'ash option changed: {key}'
run('make', '-j1', 'CC=/usr/bin/gcc', 'HOSTCC=/usr/bin/gcc', 'CROSS_COMPILE=', cwd=busybox)
run(busybox / 'busybox', 'ash', '-c', 'echo native-ash-ready')
(base / 'ash').symlink_to(busybox / 'busybox')

# The previous native UCI test container was removed; reuse existing native
# libubox/jshn, build only UCI from the locked cached source. No cmake install.
source(work / 'package/system/uci/Makefile', work / 'dl/uci-2025.12.02~66127cd7.tar.zst', 'PKG_MIRROR_HASH')
uci_src = base / 'uci-2025.12.02~66127cd7'
uci_build = base / 'build-uci'
run('/usr/bin/cmake', '-S', uci_src, '-B', uci_build, '-DBUILD_LUA=OFF',
    '-DCMAKE_C_COMPILER=/usr/bin/gcc', '-DCMAKE_PREFIX_PATH=' + str(host),
    '-DCMAKE_BUILD_RPATH=' + str(host / 'lib'))
run('/usr/bin/cmake', '--build', uci_build, '--target', 'cli', '-j1')
env['LD_LIBRARY_PATH'] = str(uci_build) + ':' + str(host / 'lib')
# These paths belong only to the disposable task container's writable layer.
for directory in ('/etc/config', '/tmp/.uci', '/var/state', '/lib/functions', '/lib/config', '/usr/share/libubox'):
    Path(directory).mkdir(parents=True, exist_ok=True)
for src, dest in ((uci_build / 'uci', '/usr/bin/uci'),
                  (host / 'bin/jshn', '/usr/bin/jshn'),
                  (work / 'package/libs/libubox/sh/jshn.sh', '/usr/share/libubox/jshn.sh'),
                  (work / 'package/base-files/files/lib/functions.sh', '/lib/functions.sh'),
                  (work / 'package/system/uci/files/lib/config/uci.sh', '/lib/config/uci.sh')):
    assert not Path(dest).exists(), f'unexpected installed fixture file: {dest}'
    Path(dest).symlink_to(src)
for name in ('uci-defaults.sh', 'ipv4.sh'):
    Path('/lib/functions', name).symlink_to(work / 'package/base-files/files/lib/functions' / name)
Path('/sbin/uci').symlink_to('/usr/bin/uci')
env['BOLTWRT_TEST_SHELL'] = str(base / 'ash')
run('/usr/bin/python3', '/review/test_defaults.py')
identity = {'busybox_version': '1.37.0', 'ash_sha256': hashlib.sha256((busybox / 'busybox').read_bytes()).hexdigest(),
            'shell_config': symbols, 'patch_sha256': patch_hashes,
            'target_config_sha256': hashlib.sha256((work / '.config').read_bytes()).hexdigest(),
            'harness_sha256': hashlib.sha256(Path('/review/test_defaults.py').read_bytes()).hexdigest(),
            'tests': 'seven native UCI/JSHN tests via native ash; see runner.log',
            'scope': 'native interpreter semantics only; no target binary or router boot'}
identity['native_reused_sha256'] = {
    str(path): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (host / 'bin/jshn', host / 'lib/libubox.so', host / 'lib/libblobmsg_json.so',
                 work / 'package/libs/libubox/Makefile')}
identity['space_measurement'] = 'du allocated bytes, sampled; instantaneous peak may be missed; tmpfs hard cap 200MiB'
identity['final_allocated_bytes'] = 0
for _ in range(3):
    identity['peak_sampled_allocated_bytes'] = peak_sampled_bytes
    (base / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n')
    identity['final_allocated_bytes'] = budget()
(base / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n')
print(json.dumps(identity), flush=True)
