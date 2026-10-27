#!/usr/bin/env python3
"""Local QEMU fixture: full x86 disks or an explicitly separate ARM kernel/root.

Run inside the task container. Its published management ports must be bound to
host loopback. This is a test tool, not a product image or deployment service.
"""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import time


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def launch(args):
    if os.geteuid() == 0:
        raise ValueError("run QEMU as an unprivileged user")
    source = args.source.resolve(strict=True)
    if digest(source) != args.sha256:
        raise ValueError("source SHA256 mismatch")
    # Never overwrite a previous attempt, its disk, or its failure evidence.
    args.case.mkdir(parents=True, exist_ok=False)
    args.evidence.mkdir(parents=True, exist_ok=False)
    disk = args.case / ("disk.vmdk" if args.format == "vmdk" else "disk.img")
    compressed = source
    preparation = {"source_sha256": args.sha256}
    if args.fwtool:
        if args.format != "raw" or source.suffix != ".gz":
            raise ValueError("fwtool stripping is only for a signed gzip disk")
        compressed = args.case / "unsigned.img.gz"
        with compressed.open("wb") as output:
            stripped = subprocess.run([str(args.fwtool), "-s", "/dev/null", "-T", str(source)],
                                      stdout=output, stderr=subprocess.PIPE, text=False)
        preparation.update(fwtool_sha256=digest(args.fwtool), fwtool_exit=stripped.returncode,
                           fwtool_stderr=stripped.stderr.decode(errors="replace"),
                           unsigned_gzip_sha256=digest(compressed),
                           signature_scope="fwtool CRC/format only; no usign cryptographic verification")
        save(args.evidence / "preparation.json", preparation)
        if stripped.returncode:
            raise RuntimeError("fwtool signature extraction/CRC check failed")
    if args.format == "raw" and source.suffix == ".gz":
        with gzip.open(compressed, "rb") as src, disk.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    else:
        shutil.copyfile(source, disk)
    info = json.loads(subprocess.check_output(
        ["qemu-img", "info", "--output=json", str(disk)]))
    if info["format"] != args.format or "backing-filename" in info:
        raise ValueError("unexpected disk format or external backing file")
    preparation.update(prepared_disk_sha256=digest(disk), prepared_disk_bytes=disk.stat().st_size)
    save(args.evidence / "preparation.json", preparation)
    serial = args.case / "serial.sock"
    qmp = args.case / "qmp.sock"
    if len(os.fsencode(serial)) >= 100 or any("," in str(p) for p in (disk, serial, qmp, args.evidence)):
        raise ValueError("QEMU fixture paths are too long or contain commas")
    command = ["qemu-system-" + args.arch, "-accel", "tcg", "-smp", "1",
               "-m", str(args.memory), "-display", "none", "-monitor", "none",
               "-daemonize", "-pidfile", str(args.case / "qemu.pid"),
               "-qmp", f"unix:{qmp},server=on,wait=off",
               "-chardev", f"socket,id=console,path={serial},server=on,wait=off,logfile={args.evidence / 'serial.log'}",
               "-serial", "chardev:console"]
    if args.arch == "x86_64":
        if args.kernel:
            raise ValueError("x86 must boot the complete disk, not -kernel")
        command += ["-machine", "pc", "-cpu", "qemu64", "-drive",
                    f"file={disk},format={args.format},if=ide"]
        if args.efi_code or args.efi_vars:
            if not (args.efi_code and args.efi_vars):
                raise ValueError("UEFI requires matching CODE and VARS")
            code = args.efi_code.resolve(strict=True)
            original_vars = args.efi_vars.resolve(strict=True)
            variables = args.case / "OVMF_VARS.fd"
            shutil.copyfile(original_vars, variables)
            command += ["-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
                        "-drive", f"if=pflash,format=raw,unit=1,file={variables}"]
        device = "e1000"
    else:
        if not args.kernel or args.format != "raw" or args.efi_code or args.efi_vars:
            raise ValueError("ARM fixture requires a generic kernel and raw rootfs")
        command += ["-machine", "virt", "-cpu", "cortex-a57", "-kernel",
                    str(args.kernel.resolve(strict=True)), "-drive",
                    f"file={disk},format=raw,if=virtio", "-append",
                    "console=ttyAMA0 root=/dev/vda rootwait rootfstype=ext4 rw"]
        device = "virtio-net-pci"
    network = ("user,id=lan,net=10.126.0.0/18,host=10.126.0.2,dns=10.126.0.3,"
               "dhcpstart=10.126.0.10,"
               f"hostfwd=tcp:0.0.0.0:{args.ssh_port}-10.126.0.1:22,"
               f"hostfwd=tcp:0.0.0.0:{args.http_port}-10.126.0.1:80,"
               f"hostfwd=tcp:0.0.0.0:{args.https_port}-10.126.0.1:443")
    command += ["-netdev", network, "-device", device + ",netdev=lan"]
    record = {"source": str(source), "source_sha256": args.sha256,
              "disk_info": info, "command": command, "arch": args.arch,
              "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "kernel_sha256": digest(args.kernel) if args.kernel else None,
              "efi_code_sha256": digest(args.efi_code) if args.efi_code else None,
              "efi_vars_source_sha256": digest(args.efi_vars) if args.efi_vars else None}
    save(args.evidence / "launch.json", record)
    result = subprocess.run(command, capture_output=True, text=True)
    record.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    save(args.evidence / "launch.json", record)
    if result.returncode:
        raise RuntimeError(result.stderr)
    print(json.dumps({"launched": str(args.case), "evidence": str(args.evidence)}))


def execute(args):
    started = time.monotonic()
    phase = "input"
    received = bytearray()
    try:
        result = execute_serial(args, received)
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        # Never persist the command or password, including on setup failures.
        result = {"label": args.label, "status": "error", "returncode": None,
                  "phase": getattr(error, "runtime_phase", phase),
                  "error_type": type(error).__name__, "error": str(error),
                  "output": received.decode(errors="replace")}
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["input_mode"] = "public-echo-permitted" if args.public_input else "private-requires-noecho"
    if args.result:
        save(args.result, result)
    print(json.dumps(result, ensure_ascii=False))
    return result["returncode"] if result["returncode"] is not None else 1


def execute_serial(args, received):
    phase = "input"
    command = args.command_file.read_text() if args.command_file else args.command
    if command is None or any(len(line.encode()) > 2000 for line in command.splitlines()):
        raise ValueError("a command is required; serial lines must be <=2000 bytes")
    nonce = "BOLT_" + secrets.token_hex(12)
    end = time.monotonic() + args.timeout
    password = args.password_file.read_text().rstrip("\n") if args.password_file else None
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            phase = "serial-connect"
            if not (args.case / "qemu.pid").is_file():
                raise RuntimeError("case has not been successfully launched")
            while not (args.case / "serial.sock").exists():
                if time.monotonic() >= end:
                    raise TimeoutError("serial socket did not appear")
                time.sleep(0.25)
            connection.connect(str(args.case / "serial.sock"))
            connection.settimeout(1)
            last_enter = 0.0
            prompted = False
            phase = "guest-login"
            while time.monotonic() < end:
                if time.monotonic() - last_enter > 3:
                    connection.sendall(b"\n")
                    last_enter = time.monotonic()
                try:
                    data = connection.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    raise RuntimeError("serial disconnected before shell prompt")
                received.extend(data)
                tail = bytes(received[-4096:])
                if re.search(rb"(?:^|[\r\n])[^\r\n]*login: *$", tail):
                    connection.sendall(b"root\n")
                elif tail.rstrip().endswith(b"Password:"):
                    if password is None:
                        raise ValueError("guest requires a private password file")
                    connection.sendall(password.encode() + b"\n")
                elif re.search(rb"root@[^\r\n]*[#$] *$", tail):
                    prompted = True
                    break
            if not prompted:
                raise TimeoutError("guest shell prompt not reached")
            # Confirm echo is off before sending any potentially private command.
            # A complete output line cannot match the echoed printf command.
            phase = "echo-off-handshake"
            ready = (nonce + "_ECHO_OFF").encode()
            unavailable = (nonce + "_NO_ECHO_CONTROL").encode()
            connection.sendall(
                b"if command -v stty >/dev/null 2>&1 && stty -echo; then printf '\\n"
                + ready + b"\\n'; else printf '\\n" + unavailable + b"\\n'; fi\n")
            ready_pattern = re.compile(rb"(?:^|[\r\n])" + ready + rb"\r?\n")
            unavailable_pattern = re.compile(rb"(?:^|[\r\n])" + unavailable + rb"\r?\n")
            received.clear()
            while time.monotonic() < end:
                try:
                    data = connection.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    raise RuntimeError("serial disconnected while disabling echo")
                received.extend(data)
                if ready_pattern.search(received):
                    break
                if unavailable_pattern.search(received):
                    if not args.public_input:
                        raise RuntimeError("guest has no echo control; private input refused")
                    break
            else:
                raise TimeoutError("echo-off acknowledgement not received")
            received.clear()
            phase = "command"
            # Errexit catches intermediate failures without killing the console.
            delimiter = nonce + "_SCRIPT"
            wrapped = ("/bin/ash -e <<'" + delimiter + "'\n" + command + "\n" + delimiter
                       + "\nruntime_status=$?\nprintf '\\n" + nonce + ":%s\\n' \"$runtime_status\"\n")
            connection.sendall(wrapped.encode())
            pattern = re.compile(rb"(?:^|[\r\n])" + nonce.encode() + rb":([0-9]+)\r?\n")
            while time.monotonic() < end:
                try:
                    data = connection.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    raise RuntimeError("guest disconnected before command result")
                received.extend(data)
                match = pattern.search(received)
                if match:
                    return {"label": args.label, "returncode": int(match.group(1)),
                            "status": "complete", "phase": phase,
                            "output": received[:match.start()].decode(errors="replace")}
            raise TimeoutError("guest command result timed out")
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        error.runtime_phase = phase
        raise


def quit_vm(args):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(str(args.case / "qmp.sock"))
        connection.settimeout(5)
        stream = connection.makefile("rwb")
        json.loads(stream.readline())
        for request in ("qmp_capabilities", "quit"):
            stream.write(json.dumps({"execute": request}).encode() + b"\n")
            stream.flush()
            while True:
                response = json.loads(stream.readline())
                if "error" in response:
                    raise RuntimeError(response)
                if "return" in response:
                    break
    print(json.dumps({"qmp_quit": str(args.case)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    start = actions.add_parser("launch")
    start.add_argument("--case", required=True, type=Path)
    start.add_argument("--evidence", required=True, type=Path)
    start.add_argument("--source", required=True, type=Path)
    start.add_argument("--sha256", required=True)
    start.add_argument("--format", choices=("raw", "vmdk"), required=True)
    start.add_argument("--fwtool", type=Path,
                       help="fixed native fwtool for legacy gzip signature stripping to a separate file")
    start.add_argument("--arch", choices=("x86_64", "aarch64"), default="x86_64")
    start.add_argument("--kernel", type=Path)
    start.add_argument("--efi-code", type=Path)
    start.add_argument("--efi-vars", type=Path)
    start.add_argument("--memory", type=int, default=768)
    for name, port in (("ssh", 2222), ("http", 8080), ("https", 8443)):
        start.add_argument("--" + name + "-port", type=int, default=port)
    run = actions.add_parser("exec")
    run.add_argument("--case", required=True, type=Path)
    run.add_argument("--command")
    run.add_argument("--command-file", type=Path)
    run.add_argument("--password-file", type=Path)
    run.add_argument("--public-input", action="store_true",
                     help="allow echoed commands without stty; never put secrets in these commands")
    run.add_argument("--timeout", type=int, default=600)
    run.add_argument("--label", default="guest-command")
    run.add_argument("--result", type=Path)
    stop = actions.add_parser("quit")
    stop.add_argument("--case", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "launch":
        launch(args)
        return 0
    if args.action == "exec":
        return execute(args)
    quit_vm(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
