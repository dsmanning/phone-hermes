#!/usr/bin/env bash
set -euo pipefail

BACKEND_ROOT=${BACKEND_ROOT:-/media/athena/chiron/hermes-output/hermes-dev-backend}
DOCKERFILE=$BACKEND_ROOT/Dockerfile
BACKUP_FILE=$BACKEND_ROOT/Dockerfile.before-ffmpeg
RUNTIME_ENV=$BACKEND_ROOT/.runtime.env
MODE=${1:-}

if [[ -n "$MODE" && "$MODE" != "--prepare-only" ]]; then
    printf 'Usage: %s [--prepare-only]\n' "$0" >&2
    exit 2
fi
if [[ ! -f "$BACKUP_FILE" ]]; then
    printf 'Rollback Dockerfile is missing: %s\n' "$BACKUP_FILE" >&2
    exit 1
fi

cp -p -- "$BACKUP_FILE" "$DOCKERFILE"
printf 'Restored pre-FFmpeg Dockerfile.\n'

if [[ "$MODE" == "--prepare-only" ]]; then
    exit 0
fi
if [[ ! -f "$RUNTIME_ENV" ]]; then
    printf 'Runtime environment is missing: %s\n' "$RUNTIME_ENV" >&2
    exit 1
fi

sudo docker compose --project-directory "$BACKEND_ROOT" \
    --env-file "$RUNTIME_ENV" config --quiet
sudo docker compose --project-directory "$BACKEND_ROOT" \
    --env-file "$RUNTIME_ENV" up -d --build --force-recreate dev

sudo docker exec --user 1000:1000 hermes-dev sh -eu -c '
    ! command -v ffmpeg >/dev/null 2>&1
    ! command -v ffprobe >/dev/null 2>&1
    test -w /workspace/camera-event-search
    test -r /workspace/camera-event-search/media
    test ! -w /workspace/camera-event-search/media
    test ! -e /var/run/docker.sock
    ! command -v sudo >/dev/null 2>&1
    curl -4 -fsS --connect-timeout 5 --max-time 20 \
        http://frigate.agent.lan/api/version >/dev/null
'

printf 'FFmpeg development-image rollback passed.\n'
