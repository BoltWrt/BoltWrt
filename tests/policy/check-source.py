#!/usr/bin/env python3
"""Check actual locked LuCI/Argon sources and stage the product package hook."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('prepare', ROOT / 'scripts/boltwrt-prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def original(repo, name, commit='HEAD'):
    return subprocess.check_output(['git', '-c', f'safe.directory={repo}', '-C', str(repo),
                                    'show', f'{commit}:{name}']).decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--luci', type=Path)
    parser.add_argument('--luci-archive', type=Path)
    args = parser.parse_args()
    pw = args.packages / 'luci/applications/luci-app-passwall2'
    expected_types = {'1_sing-box.lua', '2_xray.lua', '3_ss-rust.lua', '4_ssr.lua'}
    for role in ('client', 'server'):
        assert {p.name for p in (pw/f'luasrc/model/cbi/passwall2/{role}/type').glob('*.lua')} == expected_types
    recipe = (pw/'Makefile').read_text()
    for option in ('Hysteria', 'NaiveProxy', 'Shadowsocks_Libev_Client', 'Shadowsocks_Libev_Server', 'tuic_client'):
        assert 'INCLUDE_'+option not in recipe
    for choice in ('Xray', 'SingBox', 'All'):
        assert f'config PACKAGE_$(PKG_NAME)_Basic_Core_{choice}' in recipe
    for name in ('hysteria', 'naiveproxy', 'tuic-client', 'shadowsocks-libev'):
        assert not (args.packages/'net'/name).exists(), name
    manifest = json.loads((args.packages / 'compat/openwrt-25.12.json').read_text())
    theme = args.packages / 'luci/theme/luci-theme-argon'
    policy = manifest['submodules']['luci/theme/luci-theme-argon']
    patched = {}
    for name, replacements in policy['files'].items():
        patched[name] = prepare.exact_patch(original(theme, name, policy['commit']), replacements, name)
        assert (theme/name).read_text() in (original(theme, name, policy['commit']), patched[name]), name
    def luci_source(name):
        if args.luci_archive:
            with tarfile.open(args.luci_archive) as archive:
                matches = [item for item in archive if item.isfile() and item.name.endswith('/'+name)]
                assert len(matches) == 1
                return archive.extractfile(matches[0]).read().decode()
        assert args.luci
        return original(args.luci, name)
    system = prepare.exact_patch(luci_source('modules/luci-mod-system/htdocs/luci-static/resources/view/system/system.js'),
        prepare.PATCHES['luci']['modules/luci-mod-system/htdocs/luci-static/resources/view/system/system.js'], 'system.js')
    assert '_mediaurlbase' not in system
    assert "uci.get('luci', 'languages')" in system and "'_tablefilters'" in system
    lucimk = prepare.exact_patch(luci_source('luci.mk'), prepare.PATCHES['luci']['luci.mk'], 'luci.mk')
    assert lucimk.count('$(call Package/$(PKG_NAME)/install-extra,$(1))') == 1
    assert 'argon-appearance' not in patched['ucode/template/themes/argon/header.ut']
    assert 'argon-appearance' not in patched['ucode/template/themes/argon/page_scripts.ut']
    makefile = patched['Makefile']
    start = makefile.index('define Package/luci-theme-argon/install-extra\n')
    end = makefile.index('\nendef', start) + len('\nendef')
    hook = makefile[start:end]
    with tempfile.TemporaryDirectory(prefix='boltwrt-fixed-theme-') as directory:
        directory = Path(directory)
        stage = directory / 'payload'
        shutil.copytree(theme/'root', stage)
        shutil.copytree(theme/'htdocs', stage/'www', dirs_exist_ok=True)
        shutil.copytree(theme/'ucode', stage/'usr/share/ucode/luci', dirs_exist_ok=True)
        for name, value in patched.items():
            if name.startswith('ucode/'):
                (stage/'usr/share/ucode/luci'/name.removeprefix('ucode/')).write_text(value)
        harness = directory/'Makefile'
        harness.write_text('RM:=rm -f\n'+hook+'\nall:\n\t$(call Package/luci-theme-argon/install-extra,'+str(stage)+')\n')
        subprocess.run(['make', '-s', '-f', str(harness)], check=True)
        for name in ('www/luci-static/resources/view/argon-config.js',
                     'www/luci-static/resources/argon-appearance.js',
                     'usr/share/luci/menu.d/luci-app-argon-config.json',
                     'usr/share/rpcd/acl.d/luci-app-argon-config.json',
                     'usr/share/rpcd/acl.d/luci-theme-argon.json',
                     'usr/libexec/rpcd/luci.argon', 'usr/libexec/rpcd/luci.argon_update',
                     'usr/libexec/rpcd/luci.argon_appearance', 'usr/libexec/rpcd/luci.argon_branding'):
            assert not (stage/name).exists(), name
        for name in ('www/luci-static/argon/css/cascade.css', 'etc/config/argon',
                     'usr/share/ucode/luci/template/themes/argon/header.ut',
                     'usr/share/ucode/luci/argon-branding.uc',
                     'usr/libexec/rpcd/luci.argon_wallpaper'):
            assert (stage/name).is_file(), name
    print('PASS upstream backend types, options and obsolete recipe removal')
    print('PASS fixed-theme payload: no theme selector, appearance settings or updater; language and renderer retained')


if __name__ == '__main__':
    main()
