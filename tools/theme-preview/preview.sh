#!/bin/sh
set -eu

PREVIEW_TOOL=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PREVIEW_REPO=$(CDPATH= cd -- "$PREVIEW_TOOL/../.." && pwd -P)
PREVIEW_DATA="$PREVIEW_REPO/.theme-preview"
PREVIEW_THEME=$(CDPATH= cd -- "$PREVIEW_REPO/../Packages/luci/theme/luci-theme-argon" && pwd -P)
PREVIEW_CONTAINER=boltwrt-theme-preview
PREVIEW_IMAGE=boltwrt-theme-preview:local
PREVIEW_PORT=${BOLTWRT_PREVIEW_PORT:-18443}

check_owner() {
    PREVIEW_OWNER=$(docker inspect --format '{{ index .Config.Labels "io.boltwrt.theme-preview.root" }}' "$PREVIEW_CONTAINER" 2>/dev/null || true)
    if [ -n "$PREVIEW_OWNER" ] && [ "$PREVIEW_OWNER" != "$PREVIEW_REPO" ]; then
        printf '%s\n' 'Container name belongs to another workspace; refusing to modify it.' >&2
        exit 1
    fi
}

build() {
    mkdir -p "$PREVIEW_DATA/buildx"
    chmod 700 "$PREVIEW_DATA"
    BUILDX_CONFIG="$PREVIEW_DATA/buildx" docker build --pull=false --build-arg "PREVIEW_UID=$(id -u)" \
        --build-arg "PREVIEW_GID=$(id -g)" -t "$PREVIEW_IMAGE" "$PREVIEW_TOOL"
}

case "${1:-help}" in
    build)
        build
        ;;
    prepare)
        [ "$#" -eq 2 ] || { printf '%s\n' 'Usage: preview.sh prepare /path/to/BoltWrt_x86-64_v4.0.0.tar.gz' >&2; exit 1; }
        PREVIEW_INPUT_DIR=$(CDPATH= cd -- "$(dirname -- "$2")" && pwd -P)
        PREVIEW_INPUT="$PREVIEW_INPUT_DIR/$(basename -- "$2")"
        [ -f "$PREVIEW_INPUT" ] || { printf '%s\n' 'Firmware file does not exist.' >&2; exit 1; }
        mkdir -p "$PREVIEW_DATA"
        chmod 700 "$PREVIEW_DATA"
        docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
            --tmpfs /tmp:rw,nosuid,nodev,size=64m \
            --mount "type=bind,source=$PREVIEW_DATA,target=/data" \
            --mount "type=bind,source=$PREVIEW_TOOL,target=/tool,readonly" \
            --mount "type=bind,source=$PREVIEW_INPUT,target=/input/firmware.tar.gz,readonly" \
            "$PREVIEW_IMAGE" prepare
        ;;
    up)
        check_owner
        [ -f "$PREVIEW_DATA/prepared.json" ] || { printf '%s\n' 'Import the downloaded firmware with preview.sh prepare first.' >&2; exit 1; }
        if [ -n "$PREVIEW_OWNER" ]; then
            printf '%s\n' 'Preview container already exists. Use status or logs.'
            exit 0
        fi
        docker run -d --rm --init --name "$PREVIEW_CONTAINER" \
            --label "io.boltwrt.theme-preview.root=$PREVIEW_REPO" \
            --read-only --cap-drop ALL --security-opt no-new-privileges \
            --memory 1536m --cpus 2 --pids-limit 128 \
            --tmpfs /tmp:rw,nosuid,nodev,size=64m \
            --publish "127.0.0.1:$PREVIEW_PORT:8443" \
            --env "BOLTWRT_PREVIEW_PORT=$PREVIEW_PORT" \
            --mount "type=bind,source=$PREVIEW_DATA,target=/data" \
            --mount "type=bind,source=$PREVIEW_TOOL,target=/tool,readonly" \
            --mount "type=bind,source=$PREVIEW_THEME,target=/theme,readonly" \
            --mount "type=bind,source=$PREVIEW_REPO/tests/runtime,target=/runtime-tools,readonly" \
            "$PREVIEW_IMAGE" serve
        printf 'Booting preview: https://127.0.0.1:%s/\n' "$PREVIEW_PORT"
        printf '%s\n' 'Use preview.sh logs to follow first boot and theme synchronization.'
        ;;
    sync|status)
        check_owner
        docker exec "$PREVIEW_CONTAINER" python -B /tool/runtime.py "$1"
        ;;
    logs)
        check_owner
        docker logs --tail 40 "$PREVIEW_CONTAINER"
        ;;
    stop)
        check_owner
        docker stop --timeout 45 "$PREVIEW_CONTAINER"
        ;;
    *)
        printf '%s\n' 'Commands: build | prepare FIRMWARE.tar.gz | up | sync | status | logs | stop'
        ;;
esac
