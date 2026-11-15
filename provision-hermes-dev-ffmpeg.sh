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
if [[ ! -f "$DOCKERFILE" ]]; then
    printf 'Development Dockerfile is missing: %s\n' "$DOCKERFILE" >&2
    exit 1
fi
if [[ ! -f "$BACKUP_FILE" ]]; then
    cp -p -- "$DOCKERFILE" "$BACKUP_FILE"
fi

python3 - "$DOCKERFILE" <<'PY'
from pathlib import Path
import os
import re
import sys

path = Path(sys.argv[1])
content = path.read_text()
install_marker = "apt-get install -y --no-install-recommends"
cleanup_marker = "&& rm -rf /var/lib/apt/lists"

install_at = content.find(install_marker)
if install_at < 0:
    raise SystemExit("Expected apt-get install command is missing from Dockerfile")
cleanup_at = content.find(cleanup_marker, install_at)
if cleanup_at < 0:
    if re.search(
        re.escape(install_marker) + r"\s+ffmpeg(?:\s|\\|$)", content[install_at:]
    ) is None:
        content = content.replace(install_marker, install_marker + " ffmpeg", 1)
else:
    install_block = content[install_at:cleanup_at]
    if re.search(r"(?m)^\s*ffmpeg(?:\s|\\|$)", install_block) is None:
        cleanup_line_start = content.rfind("\n", install_at, cleanup_at) + 1
        previous_line_end = cleanup_line_start - 1
        previous_line_start = content.rfind("\n", install_at, previous_line_end) + 1
        previous_line = content[previous_line_start:previous_line_end]
        indentation = previous_line[: len(previous_line) - len(previous_line.lstrip())]
        content = (
            content[:cleanup_line_start]
            + indentation
            + "ffmpeg \\\n"
            + content[cleanup_line_start:]
        )

repository = path.with_name(path.name + ".ffmpeg.tmp")
repository.write_text(content)
os.chmod(repository, path.stat().st_mode)
os.replace(repository, path)
PY

printf 'Prepared FFmpeg package installation in %s\n' "$DOCKERFILE"
printf 'Rollback Dockerfile: %s\n' "$BACKUP_FILE"

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
    ffmpeg -version >/dev/null
    ffprobe -version >/dev/null
    test -w /workspace/camera-event-search
    test -r /workspace/camera-event-search/media
    test ! -w /workspace/camera-event-search/media
    test ! -e /var/run/docker.sock
    ! command -v sudo >/dev/null 2>&1
    mkdir -p /workspace/camera-event-search/artifacts
    output=/workspace/camera-event-search/artifacts/.ffmpeg-smoke.jpg
    trap "rm -f -- $output" EXIT
    ffmpeg -v error -ss 00:00:01 \
        -i "/workspace/camera-event-search/media/Ch008_Road Corner/Normal01/2026-10-05/14D/20261005-140008-140507.mp4" \
        -frames:v 1 -y "$output"
    test -s "$output"
    curl -4 -fsS --connect-timeout 5 --max-time 20 \
        http://frigate.agent.lan/api/version >/dev/null
'

printf 'FFmpeg development-image provisioning passed.\n'
