# Firmware runtime acceptance

The x86 runner defaults to candidate release 4.1.0; pass `--release 4.0.1` for
an earlier candidate. It records the expected release and passes it to native
helpers as `BOLTWRT_TEST_RELEASE`. For the ARM command below, set that variable
explicitly. The 4.0.0 baseline remains unchanged. Current firmware retains no
persistent migration backup and contains no test-candidate cleanup hook.
Preserve earlier 4.0.1 results under their original hashes;
new tag-built firmware needs its own case and evidence directories.

Run these checks in task-specific Docker containers. Build tools and runtime
dependencies are described in [the source-build guide](../../tools/build/README.md).
The checks use disposable firmware copies and local proxy peers. They require
neither host packages nor production credentials.

Keep case directories, backups, certificates and logs private. They contain
synthetic credentials and must not be committed or included in a public firmware
archive. Public build provenance and signed APK bundles are separate outputs.

## x86 full-system cases

Use `refresh_401.py` in the unprivileged runtime container. Mount the repository
at `/repo` and candidate/baseline inputs read-only at `/data`. Mount a dedicated
private case directory at `/cases`. Keep the candidate manifest next to its
images. The runner checks the exact installed package set after each candidate
boot, rather than accepting a few matching component versions.

Use one VM at a time per container: SSH, HTTP and HTTPS forwards use container
loopback ports 2223, 8081 and 8444. The QEMU network restricts guest traffic to
the local test environment. No host port publication is needed.

```sh
python3 /repo/tests/runtime/refresh_401.py \
  --case /cases/upgrade-bios --evidence /cases/evidence-upgrade-bios \
  --source /data/baseline/base-bios.vmdk --sha256 ORIGINAL_VMDK_SHA256 \
  --release 4.1.0 \
  --upgrade-image /data/candidate/boltwrt-4.1.0-x86-64-generic-squashfs-combined.img.gz \
  --upgrade-sha256 CANDIDATE_IMG_SHA256 --intent enabled \
  --apk /data/signed/luci-i18n-passwall2-zh-cn-26.11.11.apk \
  --rollback-image /data/baseline/base-bios.img.gz \
  --rollback-sha256 ORIGINAL_IMG_SHA256
```

Replace the SHA placeholders with verified input hashes. The baseline is the
official 4.0.0 image, not a disk already modified by an earlier case. For a
password-protected baseline, supply a private `--source-password-file`.

The minimum matrix is:

| Case | Inputs and checks |
| --- | --- |
| Fresh BIOS | Candidate BIOS VMDK, no `--upgrade-image`; first boot and real reboot |
| Fresh UEFI | Candidate EFI VMDK, matching `--efi-code` / `--efi-vars`; first boot and real reboot |
| BIOS upgrade | Official 4.0.0 BIOS VMDK, candidate BIOS IMG, `--intent enabled`, real reboot and original-backup rollback |
| UEFI upgrade | Official 4.0.0 EFI VMDK, candidate EFI IMG, matching OVMF pair, `--intent disabled`, real reboot |

Fresh cases check default configuration and running services, root-password
login, the served TLS certificate, Argon assets and component-update POST/CSRF
behavior. Upgrade cases add ordinary configuration-preserving `sysupgrade`
without `-F`, identity and configuration retention, absence of retired migration
backups, migrated server configurations, real Xray/Sing-Box configuration checks,
local proxy traffic and a wrong-certificate-pin rejection. Migrated Hysteria2
and SS nodes also run through the upstream Sing-Box/SS-Rust client dispatcher.
Explicit `--release 4.0.1` retains the historical candidate's expectations;
never count its earlier acceptance as evidence for the new policy. The signed
translation APK is strictly verified, a tampered copy is rejected, and a native
remove/reinstall transaction must preserve package inventory and trusted keys.
Legacy ACL checks exercise global-off intent, dynamic inherited ports and
shared/independent explicit nodes using the actual shipped ACL builder. The
full x86 client init must install nftables rules without errors, carry HTTP and
DNS traffic through local peers, survive a real dnsmasq reload and retain stable
client/DNS processes. DNS fixtures use a locally answered subdomain of
`example.com` and a public IPv4 answer; reserved `.test` names and documentation
address ranges trigger normal core or rebinding protections.

The optional rollback uses the original 4.0.0 image and its pre-upgrade backup
with ordinary `sysupgrade -f BACKUP IMAGE`, then checks old configuration bytes,
schema, package inventory and management identity. The lower-case `-f` supplies
the backup; it does not bypass image compatibility checks.

Each run needs new case and evidence directories. Preserve failed runs, serial
logs and native runtime capsules. `result.json` records uploaded input hashes,
runner hashes, boot IDs and individual checks. Only `status: passed` with a
successful process exit counts as acceptance. Shutdown sync is recorded before
QMP exits the disposable VM.

## ARM64 native userland

After the RAX source build and image/software/delivery checks pass, import the
actual generated ARM64 rootfs into a dedicated Docker image. Start a disposable
root container with `--network bridge`, `--cap-drop ALL --cap-add NET_RAW`,
`--security-opt no-new-privileges` and a read-only `/testtools` mount of
`tests/runtime/guest/`. Copy the private original-4.0.0 fixture archive and the
matching signed translation APK into that container; do not relax permissions
on host-owned private fixture files.

The native container needs a default interface for the shipped Sing-Box
automatic interface detection. A loopback-only `--network none` container
reports `network: missing default interface`. Use Docker's existing bridge
without publishing host ports. The checks use loopback peers and numeric
loopback targets; they do not need an external proxy or production endpoint.
`NET_RAW` permits the shipped Sing-Box interface/socket binding; dropping it
produced `dial tcp 127.0.0.1:19101: operation not permitted` on this Docker
kernel. No other capabilities are required by this native test.
The original-image `sysupgrade -T` check below can keep `--network none`.

With all capabilities dropped, root cannot read a host-owned mode-0600 file.
Stream each private input through `docker exec -i` into a root-owned container
file with `umask 077`; `docker cp` can retain the original host UID.

In the Python controller container, generate a signature negative control with
`python3 tests/runtime/apk_fixture.py SIGNED_TRANSLATION.apk SIGNED_METADATA_MUTANT.apk`
and copy it alongside the original signed APK. This changes one package-name
byte inside signed ADB metadata while preserving its signature and data blocks.
Changing only the compressed file's final byte can leave the entire signed
package unchanged and is not a valid signature negative control.

```sh
BOLTWRT_TEST_RELEASE=4.1.0 sh /testtools/arm-401.sh /tmp/legacy-400-fixture.tar.gz FIXTURE_SHA256 \
  /tmp/luci-i18n-passwall2-zh-cn-26.11.11.apk /testtools /tmp/signed-metadata-mutant.apk
```

Prepare the fixture using the original 4.0.0 ARM rootfs and
`lua /testtools/passwall-401.lua prepare`. Preserve its two legacy UCI files,
`/root/boltwrt-upgrade-fixture` and referenced test certificate/key, then record
the archive SHA. Use the same fixture input when comparing candidates.

The native test covers installed kernel/kmod ABI dependencies, module ELF
architecture and vermagic, real ubusd/rpcd/UCI migration and idempotence,
generated configurations, proxy traffic, certificate rejection and APK trust.
Copy `/tmp/boltwrt-arm-401-evidence.tar.gz` out before stopping the container.
Compare its complete installed package inventory with the generated manifest.
Image checks separately validate the RAX FIT images and all NAND/eMMC,
DDR3/DDR4 boot assets. Docker userland testing does not boot a RAX kernel, load
modules, write flash, or validate physical Ethernet/Wi-Fi.

Use the original 4.0.0 ARM rootfs for the upgrade-image entry check:
`legacy-rax-image-check.sh IMAGE SHA256` runs the original `sysupgrade -T`,
retains its metadata output and requires rejection after changing only the
board-name fixture. Run it first with the official 4.0.0 sysupgrade FIT as a
positive control, then with the candidate FIT. A read-only Docker rootfs and
writable `/tmp` are sufficient. The fixture supplies `cmcc,rax3000m`; it does
not emulate board hardware or execute a flash operation. Preserve the process
exit and output for both images.

## Browser rendering

Build `tools/build/Dockerfile.browser` using the project-local buildx path.
After an upgrade case passes, resume its accepted disk with the same QEMU
configuration and a new serial-log filename, retaining strict SSH host-key
checking. Run the browser image in the QEMU container's network namespace:

```sh
docker run --rm --network container:QEMU_CONTAINER \
  --cap-drop ALL --security-opt no-new-privileges \
  --mount type=bind,src=PRIVATE_CASES,dst=/cases \
  boltwrt-browser-check:local \
  /cases/upgrade-uefi /cases/evidence-upgrade-uefi /cases/browser-uefi
```

Use an absolute project path for `PRIVATE_CASES` and a new browser output
directory. The browser performs root login and renders nine Argon/PassWall2
pages, captures screenshots and rejects uncaught JavaScript or failed local
assets. It reads the accepted case's synthetic password. Only the test
certificate's SPKI is allowed, after matching the certificate digest already
authenticated over SSH by the parent run. No host certificate trust is changed.
The browser saves no configuration. Sync and stop the VM when finished.
