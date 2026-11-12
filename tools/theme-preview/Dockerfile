FROM python:3.12-slim-bookworm

ARG PREVIEW_UID=1000
ARG PREVIEW_GID=1000
ARG DEBIAN_MIRROR=https://mirrors.ustc.edu.cn

# Tools are installed only in this task's image. The host needs only Docker.
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl openssh-client qemu-system-x86 qemu-utils \
    && rm -rf /var/lib/apt/lists/* \
    && (getent group "${PREVIEW_GID}" || groupadd --gid "${PREVIEW_GID}" preview) \
    && useradd --uid "${PREVIEW_UID}" --gid "${PREVIEW_GID}" --home-dir /data --no-create-home preview

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
LABEL io.boltwrt.role="theme-preview"
USER preview
WORKDIR /data
ENTRYPOINT ["python", "-B", "/tool/runtime.py"]
