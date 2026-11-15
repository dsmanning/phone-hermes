#!/usr/bin/env bash
set -euo pipefail

BACKEND_ROOT=${BACKEND_ROOT:-/media/athena/chiron/hermes-output/hermes-dev-backend}
SQUID_FILE=$BACKEND_ROOT/squid.conf
SQUID_BACKUP=$BACKEND_ROOT/squid.conf.before-homeassistant-access
RUNTIME_ENV=$BACKEND_ROOT/.runtime.env
MODE=${1:-}

if [[ -n "$MODE" && "$MODE" != "--prepare-only" ]]; then
    printf 'Usage: %s [--prepare-only]\n' "$0" >&2
    exit 2
fi
if [[ ! -f "$SQUID_BACKUP" ]]; then
    printf 'Rollback file is missing: %s\n' "$SQUID_BACKUP" >&2
    exit 1
fi
cp -fp -- "$SQUID_BACKUP" "$SQUID_FILE"
printf 'Restored pre-Home-Assistant Squid policy from %s\n' "$SQUID_BACKUP"

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
sudo docker exec hermes-dev-egress \
    /usr/sbin/squid -k parse -f /etc/squid/squid.conf
sudo docker exec --user 1000:1000 hermes-dev sh -eu -c '
    status=$(curl -4 -sS --connect-timeout 5 --max-time 20 \
        -o /dev/null -w "%{http_code}" http://homeassistant.agent.lan/api/)
    test "$status" = 403
    curl -4 -fsS --connect-timeout 5 --max-time 20 \
        http://frigate.agent.lan/api/version >/dev/null
    curl -4 -fsS --connect-timeout 5 --max-time 20 \
        https://github.com/ >/dev/null
'
printf 'Home Assistant proxy exception rolled back; Frigate and public HTTPS checks passed.\n'
