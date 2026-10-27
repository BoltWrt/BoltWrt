# Local full-system runtime fixtures

These tools run inside a dedicated Docker container. Install QEMU and OVMF in
that container, never on the host. Use a private Docker volume for writable VM
disks, mount product inputs read-only, and publish management ports on host
`127.0.0.1` only. The helper refuses root execution and always uses TCG; it does
not require `/dev/kvm`. Keep one VM active per container because its forwarding
ports are shared.

Start `python3 tcp_forward.py` once in that container before accessing the
published management ports. It relays container ports 2222/8080/8443 through
127.0.0.1 to the launcher's internal ports 2223/8081/8444. SLIRP can otherwise
preserve a Docker bridge client's source address, to which the fresh guest LAN
has no route. The relay preserves the product's default network configuration;
it forwards opaque TCP bytes, has bounded connections/timeouts, and logs no
payload. Continue to publish Docker ports on host loopback only.

Build `Dockerfile` with the project's existing Ubuntu builder as `BASE_IMAGE`
when its native OpenWrt host tools are needed. On restricted macOS hosts set
`BUILDX_CONFIG` to a task directory inside the project before invoking Docker
build. Preserve unrelated images, volumes and build caches.

`qemu_runtime.py launch` requires an exact input SHA256, a new `--case` directory
on the private volume and a new `--evidence` directory. It copies the complete
disk; it does not use backing files or modify firmware inputs. For x86 test both
`--format raw` (including gzip input) and `--format vmdk`, with BIOS and with a
matching `--efi-code` / `--efi-vars` pair. Each UEFI case gets its own writable
VARS copy. x86 boots its full disk, including the firmware's bootloader.

Older signed gzip images may require `--fwtool /path/to/fixed/native/fwtool`.
The helper checks the original input hash, extracts its fwtool signature to
`/dev/null` with `-T`, writes a separate gzip, and reads that gzip to EOF with
CRC verification. `preparation.json` records the tool and resulting disk hashes.
This format/CRC check does not authenticate an usign signature.

For the separate ARM fixture use `--arch aarch64 --format raw --kernel ...`
with the generic armsr/armv8 kernel and ext4 rootfs. This runs a complete generic
system on QEMU `virt`; it does not test a MediaTek board, flash layout or Wi-Fi.
Build the fixture as described in `config/testing/README.md` when available.

After launch succeeds, run `exec --case ... --command-file ... --result ...`.
The serial controller waits for the guest shell, runs an ash child with
`errexit`, and records the actual command return code or a structured error.
It does not treat the mere presence of a shell as service readiness. Check
the relevant guest state before recording acceptance. Use `quit --case ...`
for QMP shutdown after a test; use a guest `reboot` when testing reboot behavior.

By default commands require working terminal echo control. Minimal BusyBox
images may lack `stty`; `--public-input` explicitly permits echoed commands.
Only use that mode for public test scripts without credentials or private-key
contents. A login password, if needed, must come from `--password-file` and is
handled by the guest login prompt. Preserve serial logs and failed-case disks;
never include real user configuration or secrets in these fixtures.

Runtime evidence must distinguish fresh installation, reboot, real APK version
upgrade, LuCI interaction and an actual guest `sysupgrade`. Build success,
source inspection and generic ARM execution do not close hardware acceptance.

The locked LuCI APK backend downloads a single HTTP(S) package URL to a private
temporary `.apk` before invoking the normal installer. Test a separately signed
copy trusted by the guest, an unsigned package, a missing URL, an empty response,
a self-signed HTTPS endpoint and a URL mixed with another package argument.
Only the trusted signed copy may install; failures must leave the installed
database and world unchanged. Repository index signatures do not make an
unsigned package trusted when installed as a standalone file. Never add
`--allow-untrusted` to the backend or its installation/verification tests.
The downloader's 30-second timeout covers connection/request inactivity, not
an overall wall-clock deadline. Check that request directories are cleaned and
the shared lock inode remains present after requests complete.
