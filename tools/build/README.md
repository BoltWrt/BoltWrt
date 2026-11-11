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
```

The pinned official Go image provides only the compiler seed in
`/opt/go-bootstrap`. OpenWrt builds Go **1.27.4 from source**, then uses that
compiler for every selected Go application. An external seed is necessary on
ARM64 build hosts because the upstream Go 1.4 bootstrap cannot run there. It
does not change the compiler used for firmware programs. The final software
check reads Go build information from the actual rootfs ELF files and rejects
a different compiler version.

The default OpenWrt bootstrap chain is still available on supported build hosts
by omitting `--go-bootstrap-root`. Product selections are identical; compiler
seed details are recorded separately in `logs/boltwrt/*-config.json`.

Keep each product's configuration and evidence before switching targets. Do
not run two builds concurrently in the same source tree. Container resource
limits and build concurrency should leave memory for other local workloads.
Runtime verification uses disposable disks derived from the official 4.0.0
release, followed by ordinary, configuration-preserving `sysupgrade` without
`-F`. A successful source build alone is not upgrade acceptance.
