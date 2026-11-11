#!/usr/bin/env python3
"""Run one disposable x86 fresh/4.0.0 upgrade case inside Docker, without -F.

Case directories contain private test credentials, backups and logs. Run as an
unprivileged container user; source images remain read-only and are copied by
qemu_runtime. Hardware flash and external proxy services are outside this test.
"""
import argparse
import hashlib
import html
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import ssl
import subprocess
import time
from types import SimpleNamespace
import urllib.parse

import qemu_runtime as qemu

GUEST = Path(__file__).with_name("guest")


class Case:
    def __init__(self, args):
        self.args = args
        self.results = {"status": "running", "steps": {}, "boot_ids": []}
        self.phase = "preflight"
        self.known = False
        self.launched = False
        self.password = secrets.token_urlsafe(24)

    def save(self):
        qemu.save(self.args.evidence / "result.json", self.results)

    def step(self, label, command, *, payload=None, timeout=180):
        self.phase = label
        result = self.ssh(command, payload=payload, timeout=timeout)
        (self.args.evidence / (label + ".log")).write_bytes(result.stdout + result.stderr)
        self.results["steps"][label] = {"returncode": result.returncode}
        self.save()
        if result.returncode:
            raise RuntimeError("guest step failed: " + label)
        print(label + ": passed", flush=True)
        return result.stdout

    def ssh(self, command, *, payload=None, timeout=30):
        return subprocess.run([
            "ssh", "-F", "/dev/null", "-i", str(self.args.case / "id_ed25519"),
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "LogLevel=ERROR",
            "-o", "StrictHostKeyChecking=" + ("yes" if self.known else "accept-new"),
            "-o", "UserKnownHostsFile=" + str(self.args.case / "known_hosts"),
            "-o", "ConnectTimeout=5", "-p", "2223", "root@127.0.0.1", command,
        ], input=payload, capture_output=True, timeout=timeout)

    def ready(self, release, previous=None):
        deadline = time.monotonic() + 900
        command = "cat /proc/sys/kernel/random/boot_id; . /etc/openwrt_release; printf '%s\\n' \"$DISTRIB_RELEASE\"; ubus call system board >/dev/null"
        while time.monotonic() < deadline:
            try:
                result = self.ssh(command, timeout=12)
                lines = result.stdout.decode().splitlines()
                if result.returncode == 0 and len(lines) == 2 and lines[1] == release and lines[0] != previous:
                    assert re.fullmatch(r"[0-9a-f-]{36}", lines[0])
                    self.known = True
                    self.results["boot_ids"].append(lines[0])
                    self.save()
                    return lines[0]
            except (OSError, subprocess.TimeoutExpired):
                pass
            time.sleep(2)
        raise TimeoutError("expected release and new boot did not become ready")

    def upload(self, local, remote):
        result = self.ssh("umask 077; cat > " + shlex.quote(remote), payload=Path(local).read_bytes(), timeout=180)
        if result.returncode:
            raise RuntimeError("fixture upload failed")

    def http(self, method, path, data=None, cookie=None):
        connection = http.client.HTTPSConnection("127.0.0.1", 8444, context=self.context, timeout=90)
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        if data is not None:
            data = urllib.parse.urlencode(data)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            connection.request(method, path, data, headers)
            response = connection.getresponse()
            body = response.read()
            return response.status, {key.lower(): value for key,value in response.getheaders()}, body
        finally:
            connection.close()

    def web(self, label, *, upgraded, candidate=True):
        self.phase = label
        certificate_path = self.step(label + "-cert-path", "uci -q get nginx._lan.ssl_certificate").decode().strip()
        assert certificate_path.startswith(("/etc/", "/root/boltwrt-upgrade-fixture/"))
        result = self.ssh("cat " + shlex.quote(certificate_path))
        assert result.returncode == 0
        certificate = result.stdout.decode()
        der = ssl.PEM_cert_to_DER_cert(certificate)
        fingerprint = hashlib.sha256(der).hexdigest()
        expected = self.results.get("served_certificate_sha256")
        if expected:
            assert fingerprint == expected, "preserved certificate file changed"
        self.context = ssl.create_default_context(cadata=certificate)
        self.context.check_hostname = False  # Exact SSH-authenticated certificate; forwarded IP differs.
        deadline = time.monotonic() + 180
        while True:
            try:
                status, _, _ = self.http("GET", "/cgi-bin/luci/")
                if status in (200, 403):
                    break
            except (OSError, ssl.SSLError, http.client.HTTPException):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("LuCI HTTPS not ready")
            time.sleep(2)
        # The network handshake must serve the exact certificate, not merely a
        # different leaf signed by a trusted CA.
        import socket
        with socket.create_connection(("127.0.0.1", 8444), timeout=20) as raw:
            with self.context.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
                assert hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest() == fingerprint
        status, headers, _ = self.http("POST", "/cgi-bin/luci/", {
            "luci_username": "root", "luci_password": self.password,
        })
        assert status in (302, 303) and "set-cookie" in headers, "actual root-password login failed"
        cookie = headers["set-cookie"].split(";", 1)[0]
        base = "/cgi-bin/luci/admin/services/passwall2/"
        pages = ["/cgi-bin/luci/admin/status/overview", base + "settings", base + "app_update"]
        if not candidate:
            pages = pages[:1]
        if upgraded:
            pages += [base + "node_config/bw401_hysteria", base + "node_config/bw401_ss", base + "node_config/bw401_xray_tls", base + "server_config/bw401_vless"]
        bodies = {}
        for number, page in enumerate(pages):
            code, _, body = self.http("GET", page, cookie=cookie)
            text = body.decode(errors="replace")
            assert code == 200 and 'name="luci_password"' not in text
            assert not any(error in text for error in ("Internal Server Error", "Unhandled exception", "Runtime error"))
            (self.args.evidence / f"{label}-page-{number}.html").write_bytes(body)
            bodies[page] = text
        assets = re.findall(r'(?:src|href)=["\']([^"\']*/luci-static/argon/[^"\']*)', bodies[pages[0]])
        assert assets, "Argon asset links absent"
        for asset in list(dict.fromkeys(assets))[:5]:
            code, _, body = self.http("GET", html.unescape(asset), cookie=cookie)
            assert code == 200 and body
        if candidate:
            app = bodies[base + "app_update"]
            token = re.search(r"var tokenStr = '([^']+)'", app)
            assert token and token[1] and "<%" not in token[1]
            update = base + "update_xray"
            code, _, _ = self.http("GET", update + "?task=progress&id=fixture", cookie=cookie)
            assert code in (400, 403, 405), "component mutation accepted GET"
            code, _, _ = self.http("POST", update, {"task": "progress", "id": "fixture"}, cookie=cookie)
            assert code in (400, 403), "component mutation accepted missing token"
            code, _, _ = self.http("POST", update, {"task": "progress", "id": "fixture", "token": "invalid"}, cookie=cookie)
            assert code in (400, 403), "component mutation accepted invalid token"
            code, _, body = self.http("POST", update, {"task": "progress", "id": "fixture", "token": token[1]}, cookie=cookie)
            assert code == 200 and isinstance(json.loads(body), dict), "valid component POST rejected"
        self.results["served_certificate_sha256"] = fingerprint
        self.results["steps"][label] = {"pages": len(pages), "argon_assets": min(5, len(set(assets))),
                                        "root_password_login": True, "tls_certificate_match": True,
                                        "component_post_security_checked": candidate}
        self.save()
        print(label + ": passed", flush=True)

    def verify_upgrade(self, label):
        for name in ("verify-upgrade-fixture.sh", "passwall-401.lua"):
            self.upload(GUEST / name, "/tmp/" + name)
        self.step(label + "-settings", f"sh /tmp/verify-upgrade-fixture.sh {self.args.intent} 4.0.1")
        self.step(label + "-passwall", "lua /tmp/passwall-401.lua verify")
        state = self.step(label + "-identity-files", self.identity_command).decode()
        assert state == self.identity, "retained password/SSH identity changed"
        self.web(label + "-web", upgraded=True)

    def run(self):
        args = self.args
        os.umask(0o077)
        if args.upgrade_image:
            assert qemu.digest(args.upgrade_image) == args.upgrade_sha256
        if args.rollback_image:
            assert args.upgrade_image and qemu.digest(args.rollback_image) == args.rollback_sha256
        launch = SimpleNamespace(case=args.case, evidence=args.evidence, source=args.source,
                                 sha256=args.sha256, format="vmdk", fwtool=None, arch="x86_64",
                                 kernel=None, efi_code=args.efi_code, efi_vars=args.efi_vars,
                                 memory=args.memory, ssh_port=2223, http_port=8081,
                                 https_port=8444, restrict_network=True)
        self.phase = "launch"
        qemu.launch(launch)
        self.launched = True
        self.save()
        qemu.save(args.case / "credentials.json", {"username": "root", "password": self.password})
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(args.case / "id_ed25519")], check=True)
        public = (args.case / "id_ed25519.pub").read_text().strip()
        command = "umask 077; mkdir -p /etc/dropbear; printf '%s\\n' " + shlex.quote(public) + " >> /etc/dropbear/authorized_keys"
        self.phase = "bootstrap"
        result = qemu.execute_serial(SimpleNamespace(case=args.case, command=command, command_file=None,
            password_file=args.source_password_file, timeout=600, public_input=True,
            label="refresh-401-public-key-bootstrap"), bytearray())
        qemu.save(args.evidence / "bootstrap.json", result)
        assert result["returncode"] == 0
        source_release = "4.0.0" if args.upgrade_image else "4.0.1"
        boot = self.ready(source_release)
        self.step("set-test-password", "passwd root >/dev/null 2>&1", payload=(self.password + "\n") .encode() * 2)
        self.identity_command = "set -e; grep '^root:' /etc/shadow | sha256sum; sha256sum /etc/dropbear/authorized_keys; for path in /etc/dropbear/*_host_key; do [ ! -f \"$path\" ] || sha256sum \"$path\"; done"
        if args.upgrade_image:
            for name in ("prepare-312-fixture.sh", "passwall-401.lua"):
                self.upload(GUEST / name, "/tmp/" + name)
            self.step("prepare-common-400", f"sh /tmp/prepare-312-fixture.sh {args.intent} 4.0.0")
            self.step("prepare-passwall-400", "umask 077; lua /tmp/passwall-401.lua prepare")
            self.step("activate-fixture-certificate", "/etc/init.d/nginx reload")
            self.identity = self.step("original-identity-files", self.identity_command).decode()
            self.original_packages = self.step("original-package-inventory", "apk list --installed | LC_ALL=C sort")
            self.web("before-upgrade-web", upgraded=False, candidate=False)
            self.step("backup-final-400", "sysupgrade -b /tmp/boltwrt-400-backup.tar.gz")
            backup = self.ssh("cat /tmp/boltwrt-400-backup.tar.gz", timeout=180)
            assert backup.returncode == 0
            (args.case / "original-400-backup.tar.gz").write_bytes(backup.stdout)
            self.upload(args.upgrade_image, "/tmp/boltwrt-401.img.gz")
            actual = self.step("uploaded-image-hash", "sha256sum /tmp/boltwrt-401.img.gz").decode().split()[0]
            assert actual == args.upgrade_sha256
            self.step("normal-sysupgrade-test", "sysupgrade -T /tmp/boltwrt-401.img.gz", timeout=180)
            self.phase = "normal-sysupgrade"
            trigger = self.ssh("nohup /sbin/sysupgrade /tmp/boltwrt-401.img.gz >/tmp/boltwrt-upgrade.log 2>&1 </dev/null &", timeout=30)
            assert trigger.returncode in (0, 255)
            boot = self.ready("4.0.1", previous=boot)
            self.verify_upgrade("after-upgrade")
            self.step("generate-core-configs", "umask 077; lua /tmp/passwall-401.lua generate")
            self.upload(GUEST / "proxy-401.sh", "/tmp/proxy-401.sh")
            self.step("local-proxy-and-tls", "sh /tmp/proxy-401.sh", timeout=360)
            if args.apk:
                self.upload(args.apk, "/tmp/boltwrt-translation.apk")
                self.upload(GUEST / "apk-trust-401.sh", "/tmp/apk-trust-401.sh")
                self.step("native-apk-trust", "sh /tmp/apk-trust-401.sh /tmp/boltwrt-translation.apk", timeout=180)
        else:
            self.upload(GUEST / "verify-fresh.sh", "/tmp/verify-fresh.sh")
            self.step("fresh-local", "sh /tmp/verify-fresh.sh x86/64 4.0.1")
            self.web("fresh-web", upgraded=False)
        self.phase = "real-reboot"
        trigger = self.ssh("nohup /sbin/reboot >/tmp/boltwrt-reboot.log 2>&1 </dev/null &")
        assert trigger.returncode in (0, 255)
        boot = self.ready("4.0.1", previous=boot)
        if args.upgrade_image:
            self.verify_upgrade("after-reboot")
        else:
            self.upload(GUEST / "verify-fresh.sh", "/tmp/verify-fresh.sh")
            self.step("rebooted-fresh-local", "sh /tmp/verify-fresh.sh x86/64 4.0.1")
            self.web("rebooted-fresh-web", upgraded=False)
        if args.rollback_image:
            self.upload(args.rollback_image, "/tmp/boltwrt-400.img.gz")
            self.upload(args.case / "original-400-backup.tar.gz", "/tmp/boltwrt-400-backup.tar.gz")
            digest = self.step("rollback-image-hash", "sha256sum /tmp/boltwrt-400.img.gz").decode().split()[0]
            assert digest == args.rollback_sha256
            self.step("normal-rollback-image-test", "sysupgrade -T /tmp/boltwrt-400.img.gz")
            self.phase = "normal-firmware-rollback"
            trigger = self.ssh("nohup /sbin/sysupgrade -f /tmp/boltwrt-400-backup.tar.gz /tmp/boltwrt-400.img.gz >/tmp/boltwrt-rollback.log 2>&1 </dev/null &")
            assert trigger.returncode in (0,255)
            self.ready("4.0.0", previous=boot)
            self.step("rollback-rom-version", "sh -c '. /rom/etc/openwrt_release; test \"$DISTRIB_RELEASE\" = 4.0.0'")
            for name in ("verify-upgrade-fixture.sh", "passwall-401.lua"):
                self.upload(GUEST / name, "/tmp/" + name)
            self.step("rollback-settings", f"sh /tmp/verify-upgrade-fixture.sh {args.intent} 4.0.0")
            self.step("rollback-passwall", "lua /tmp/passwall-401.lua verify-rollback")
            assert self.step("rollback-identity-files", self.identity_command).decode() == self.identity
            assert self.step("rollback-package-inventory", "apk list --installed | LC_ALL=C sort") == self.original_packages
            self.web("rollback-web", upgraded=False, candidate=False)
            self.results["normal_rollback_with_original_backup"] = True
        self.results["status"] = "passed"
        self.results["scope"] = "disposable x86 full-system QEMU; no hardware flash or external proxy peer"
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("case", "evidence", "source"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--upgrade-image", type=Path)
    parser.add_argument("--upgrade-sha256")
    parser.add_argument("--intent", choices=("enabled", "disabled"), default="enabled")
    parser.add_argument("--source-password-file", type=Path)
    parser.add_argument("--efi-code", type=Path)
    parser.add_argument("--efi-vars", type=Path)
    parser.add_argument("--memory", type=int, default=1280)
    parser.add_argument("--apk", type=Path)
    parser.add_argument("--rollback-image", type=Path,
                        help="optional official 4.0.0 upgrade image to verify rollback with the original backup")
    parser.add_argument("--rollback-sha256")
    args = parser.parse_args()
    if bool(args.upgrade_image) != bool(args.upgrade_sha256):
        parser.error("upgrade image and SHA256 must be provided together")
    if bool(args.rollback_image) != bool(args.rollback_sha256) or (args.rollback_image and not args.upgrade_image):
        parser.error("rollback requires an upgrade case and the old image SHA256")
    case = Case(args)
    try:
        case.run()
        return 0
    except Exception as error:
        if args.evidence.is_dir():
            case.results.update(status="failed", phase=case.phase, error_type=type(error).__name__, error=str(error))
            case.save()
        print(json.dumps({"status": "failed", "phase": case.phase, "error_type": type(error).__name__}), flush=True)
        return 1
    finally:
        if case.launched and (args.case / "qmp.sock").exists():
            try:
                qemu.quit_vm(SimpleNamespace(case=args.case))
            except (OSError, RuntimeError):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
