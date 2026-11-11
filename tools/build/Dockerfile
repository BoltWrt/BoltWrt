# All compilation and runtime prerequisites stay inside this image.
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS certificates
FROM golang:1.26-bookworm@sha256:632dff7351799a5a9954a48dc06670ade72ee44e8c351cdc0e5a57af2fea21af AS go-bootstrap
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
COPY --from=certificates /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
ARG APT_MIRROR=https://mirrors.ustc.edu.cn
RUN sed -i "s|http://ports.ubuntu.com|${APT_MIRROR}|g; s|http://archive.ubuntu.com|${APT_MIRROR}|g; s|http://security.ubuntu.com|${APT_MIRROR}|g" /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update --error-on=any \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       build-essential clang flex bison gawk gettext git libncurses-dev \
       libssl-dev python3 python3-setuptools python3-pyelftools rsync swig \
       unzip zip zlib1g-dev file wget curl ca-certificates cmake ninja-build \
       autoconf automake libtool pkg-config patch bzip2 xz-utils zstd cpio \
       time openssh-client jq lua5.1 libjson-c-dev liblua5.1-0-dev \
       qemu-system-x86 qemu-system-arm qemu-user qemu-utils ovmf \
       device-tree-compiler e2fsprogs dosfstools mtools squashfs-tools \
    && if [ "$(dpkg --print-architecture)" = amd64 ]; then apt-get install -y --no-install-recommends gcc-multilib g++-multilib; fi \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10000 builder \
    && useradd --uid 10000 --gid 10000 --create-home --shell /bin/bash builder
# Seed only: OpenWrt builds the locked Go 1.27.4 compiler from source. Its
# historical Go 1.4 -> 1.24 bootstrap chain does not support ARM64 build hosts.
COPY --from=go-bootstrap /usr/local/go /opt/go-bootstrap
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 GOPROXY=https://goproxy.cn,direct \
    GOSUMDB=sum.golang.google.cn CARGO_REGISTRIES_CRATES_IO_PROTOCOL=sparse \
    GOMAXPROCS=3
USER 10000:10000
WORKDIR /work
CMD ["sleep", "infinity"]
