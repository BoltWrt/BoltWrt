# QEMU runs unprivileged. Use an existing build image with BASE_IMAGE to reuse
# its Ubuntu mirror and dependencies; record the resolved image identity.
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}
USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates curl openssl ovmf python3 qemu-system-arm \
       qemu-system-x86 qemu-utils \
    && rm -rf /var/lib/apt/lists/*
LABEL boltwrt.phase="4.0.0" boltwrt.role="runtime-validation"
ENV HOME=/runtime
WORKDIR /runtime
USER 10000:10000
ENTRYPOINT []
CMD ["sleep", "infinity"]
