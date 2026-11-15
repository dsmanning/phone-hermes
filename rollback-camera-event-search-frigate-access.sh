#!/usr/bin/env bash
set -euo pipefail

BACKEND_ROOT=${BACKEND_ROOT:-/media/athena/chiron/hermes-output/hermes-dev-backend}
COMPOSE_FILE=$BACKEND_ROOT/compose.yaml
SQUID_FILE=$BACKEND_ROOT/squid.conf
COMPOSE_BACKUP=$BACKEND_ROOT/compose.yaml.before-frigate-access
SQUID_BACKUP=$BACKEND_ROOT/squid.conf.before-frigate-access
RUNTIME_ENV=$BACKEND_ROOT/.runtime.env
MODE=${1:-}

if [[ -n "$MODE" && "$MODE" != "--prepare-only" ]]; then
    printf 'Usage: %s [--prepare-only]\n' "$0" >&2
    exit 2
fi
for path in "$COMPOSE_BACKUP" "$SQUID_BACKUP"; do
    if [[ ! -f "$path" ]]; then
        printf 'Rollback file is missing: %s\n' "$path" >&2
        exit 1
    fi
done

cp -p -- "$COMPOSE_BACKUP" "$COMPOSE_FILE"
cp -p -- "$SQUID_BACKUP" "$SQUID_FILE"
printf 'Restored pre-Frigate-access Compose and Squid configuration.\n'

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
    --env-file "$RUNTIME_ENV" up -d --build --force-recreate egress-proxy

if sudo docker inspect hermes-dev-egress \
    --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' \
    | grep -qw hermes-agent-lan; then
    printf 'hermes-dev-egress remains attached to hermes-agent-lan.\n' >&2
    exit 1
fi

status=$(sudo docker exec --user 1000:1000 hermes-dev sh -c '
    curl -4 -sS --connect-timeout 3 --max-time 10 \
        -o /dev/null -w "%{http_code}" \
        http://frigate.agent.lan/api/version 2>/dev/null || true
')
if [[ "$status" == "200" ]]; then
    printf 'Frigate remained accessible after rollback.\n' >&2
    exit 1
fi

printf 'Frigate access rollback passed.\n'
