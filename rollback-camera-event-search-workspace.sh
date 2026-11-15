#!/usr/bin/env bash
set -euo pipefail

BACKEND_ROOT=${BACKEND_ROOT:-/media/athena/chiron/hermes-output/hermes-dev-backend}
COMPOSE_FILE=$BACKEND_ROOT/compose.yaml
BACKUP_FILE=$BACKEND_ROOT/compose.yaml.before-camera-event-search
RUNTIME_ENV=$BACKEND_ROOT/.runtime.env
MODE=${1:-}

if [[ -n "$MODE" && "$MODE" != "--prepare-only" ]]; then
    printf 'Usage: %s [--prepare-only]\n' "$0" >&2
    exit 2
fi
if [[ ! -f "$BACKUP_FILE" ]]; then
    printf 'Rollback file is missing: %s\n' "$BACKUP_FILE" >&2
    exit 1
fi

cp -p -- "$BACKUP_FILE" "$COMPOSE_FILE"
printf 'Restored %s from %s\n' "$COMPOSE_FILE" "$BACKUP_FILE"

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
    --env-file "$RUNTIME_ENV" up -d --force-recreate dev
sudo docker exec --user 1000:1000 hermes-dev sh -eu -c '
    test -w /workspace/phone-hermes
    test ! -e /workspace/camera-event-search
'

printf 'camera-event-search workspace rollback passed. Host project and media files were not deleted.\n'
