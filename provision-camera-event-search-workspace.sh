#!/usr/bin/env bash
set -euo pipefail

BACKEND_ROOT=${BACKEND_ROOT:-/media/athena/chiron/hermes-output/hermes-dev-backend}
PROJECT_SOURCE=${PROJECT_SOURCE:-/media/athena/code/camera-event-search}
MEDIA_SOURCE=${MEDIA_SOURCE:-/media/minerva/QVR_media/File/Standard_Format}
COMPOSE_FILE=$BACKEND_ROOT/compose.yaml
BACKUP_FILE=$BACKEND_ROOT/compose.yaml.before-camera-event-search
RUNTIME_ENV=$BACKEND_ROOT/.runtime.env
MODE=${1:-}

if [[ -n "$MODE" && "$MODE" != "--prepare-only" ]]; then
    printf 'Usage: %s [--prepare-only]\n' "$0" >&2
    exit 2
fi

for path in "$BACKEND_ROOT" "$MEDIA_SOURCE"; do
    if [[ ! -d "$path" ]]; then
        printf 'Required directory is missing: %s\n' "$path" >&2
        exit 1
    fi
done
if [[ ! -f "$COMPOSE_FILE" ]]; then
    printf 'Compose file is missing: %s\n' "$COMPOSE_FILE" >&2
    exit 1
fi

mkdir -p -- "$PROJECT_SOURCE" "$PROJECT_SOURCE/media"
if [[ ! -f "$BACKUP_FILE" ]]; then
    cp -p -- "$COMPOSE_FILE" "$BACKUP_FILE"
fi

python3 - "$COMPOSE_FILE" "$PROJECT_SOURCE" "$MEDIA_SOURCE" <<'PY'
from pathlib import Path
import os
import sys

compose_path = Path(sys.argv[1])
project_source = sys.argv[2]
media_source = sys.argv[3]
phone_mount = "      - ${PHONE_HERMES_REPO}:/workspace/phone-hermes"
shared_mount = "      - /media/athena/code:/workspace/code"
project_mount = f"      - {project_source}:/workspace/camera-event-search"
media_mount = f"      - {media_source}:/workspace/camera-event-search/media:ro"

lines = compose_path.read_text().splitlines()
if phone_mount not in lines:
    raise SystemExit(f"Expected phone-hermes mount not found in {compose_path}")

lines = [
    line
    for line in lines
    if line not in {shared_mount, project_mount, media_mount}
]
insert_at = lines.index(phone_mount) + 1
lines[insert_at:insert_at] = [project_mount, media_mount]
updated = "\n".join(lines) + "\n"

temporary = compose_path.with_name(compose_path.name + ".camera-event-search.tmp")
temporary.write_text(updated)
os.chmod(temporary, compose_path.stat().st_mode)
os.replace(temporary, compose_path)
PY

printf 'Prepared scoped project and media mounts in %s\n' "$COMPOSE_FILE"
printf 'Rollback compose file: %s\n' "$BACKUP_FILE"

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
    test -w /workspace/camera-event-search
    test -r /workspace/camera-event-search/media
    if test -w /workspace/camera-event-search/media; then
        printf "QVR media mount is unexpectedly writable.\n" >&2
        exit 1
    fi
    test ! -e /workspace/code
'

printf 'camera-event-search workspace provisioning passed.\n'
