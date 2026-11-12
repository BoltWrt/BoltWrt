# Docker source builds

Build dependencies, compiler bootstrapping and QEMU stay inside Docker. No host
package manager or host configuration changes are required. Run from the main
BoltWrt repository, using a dedicated Linux volume for the build tree and `dl/`.

```sh
BUILDX_CONFIG="$PWD/.theme-preview/buildx" docker build --builder desktop-linux \
  -f tools/build/Dockerfile -t boltwrt-source-builder:local .
```

`desktop-linux` is the Docker Desktop builder used for local validation; choose
your existing Docker builder on other systems. `BUILDX_CONFIG` keeps buildx's
activity metadata in this project. The Ubuntu package mirror is configurable
with `--build-arg APT_MIRROR=https://mirrors.ustc.edu.cn`.

Inside a container started from this image, prepare the source and choose one
product target (`x86-64` or `cmcc-rax3000m`):

```sh
python3 scripts/boltwrt-prepare.py
python3 scripts/boltwrt-config.py x86-64 --go-bootstrap-root /opt/go-bootstrap
make download -j4 V=s
make -j2 V=s CONFIG_AUTOREMOVE=y
python3 scripts/boltwrt-verify.py x86-64
python3 scripts/boltwrt-software.py --build-root . --target x86-64
python3 scripts/boltwrt-delivery.py --build-root . --target x86-64
```

The pinned official Go image provides only the compiler seed in
`/opt/go-bootstrap`. OpenWrt builds Go **1.27.4 from source**, then uses that
compiler for every selected Go application. An external seed is necessary on
ARM64 build hosts because the upstream Go 1.4 bootstrap cannot run there. It
does not change the compiler used for firmware programs. The final software
check reads Go build information from the actual rootfs ELF files and rejects
a different compiler version.

The delivery step creates `boltwrt-*-build.json`, `boltwrt-*-SHA256SUMS` and an
`installed-packages.tar.gz` bundle next to the images. It selects the exact
package versions installed in that image, signs delivery copies of the APKs
with the build key, and verifies them with public keys extracted from the
actual rootfs. Use these signed copies for standalone package uploads; OpenWrt's
ordinary package output relies on signed repository indexes. The bundle
contains public keys only. Keep the build private key in the private build
workspace.

Delivery refreshes the standard OpenWrt `sha256sums` after producing the signed
bundle and provenance, so incremental builds keep both checksum lists valid.
The firmware archive also retains `profiles.json`, `config.buildinfo`,
`feeds.buildinfo` and `version.buildinfo` to identify the exact build inputs.
The standard checksum list covers this published set. Generic MediaTek RAM
loaders for other SoCs can remain in the SDK without creating references to
unpublished files in the firmware archive.

Archive each target's images, delivery bundle, checksum file and `logs/boltwrt/`
before switching configurations. `build.json` records the source commit,
locked feeds, downstream patches and actual payload checks. Its scope is build
and artifact verification; retain runtime results separately.

The default OpenWrt bootstrap chain is still available on supported build hosts
by omitting `--go-bootstrap-root`. Product selections are identical; compiler
seed details are recorded separately in `logs/boltwrt/*-config.json`.

Keep each product's configuration and evidence before switching targets. Do
not run two builds concurrently in the same source tree. Container resource
limits and build concurrency should leave memory for other local workloads.
Runtime verification uses disposable disks derived from the official 4.0.0
release, followed by ordinary, configuration-preserving `sysupgrade` without
`-F`. A successful source build alone is not upgrade acceptance.

For QEMU cases with host-mounted evidence, reuse the same tools in a runtime
image whose unprivileged account matches the evidence directory owner:

```sh
BUILDX_CONFIG="$PWD/.theme-preview/buildx" docker build --builder desktop-linux \
  -f tools/build/Dockerfile.runtime -t boltwrt-runtime:local \
  --build-arg RUNTIME_UID="$(id -u)" --build-arg RUNTIME_GID="$(id -g)" .
```

The default parent is `boltwrt-source-builder:local`; override `BUILDER_IMAGE`
if using a different local tag. This step installs no additional packages.
Run `tests/runtime/refresh_401.py --help` inside the runtime container for the
fresh-install, upgrade and optional rollback inputs. Keep its case and evidence
directories private: they contain test passwords, keys and original backups.
Keep the generated package manifest beside the candidate images; the runner
compares every installed package version with it after boot and reboot.

The [runtime acceptance guide](../../tests/runtime/README.md) describes the
minimum BIOS/UEFI matrix, original-backup rollback, native ARM64 userland checks
and isolated Chromium rendering. Keep these results alongside the exact
candidate's build and package manifests.

The [4.0.1 candidate acceptance record](acceptance-4.0.1.md) records the actual
source commits, artifact hashes, completed Docker cases and their scope.
