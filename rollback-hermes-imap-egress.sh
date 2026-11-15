#!/usr/bin/env bash
set -euo pipefail

ROOT=/media/athena/chiron/hermes-output/hermes-dev-backend
BACKUP_SQUID=$ROOT/squid.conf.before-hermes-imap
BACKUP_TEST=$ROOT/tests/test_artifacts.py.before-hermes-imap

for path in "$BACKUP_SQUID" "$BACKUP_TEST"; do
    if [[ ! -f $path ]]; then
        printf 'Rollback file is missing: %s\n' "$path" >&2
        exit 1
    fi
done

install -m 0644 "$BACKUP_SQUID" "$ROOT/squid.conf"
install -m 0644 "$BACKUP_TEST" "$ROOT/tests/test_artifacts.py"
cd "$ROOT"
python3 -m unittest discover -s tests -q
docker compose --env-file "$ROOT/.runtime.env" build egress-proxy
docker compose --env-file "$ROOT/.runtime.env" run --rm --no-deps \
    egress-proxy /usr/sbin/squid -k parse -f /etc/squid/squid.conf
docker compose --env-file "$ROOT/.runtime.env" up -d --no-deps --force-recreate egress-proxy
docker inspect hermes-dev-egress --format '{{if .State.Running}}running{{else}}not-running{{end}}' \
    | grep -Fxq running
printf 'Hyperion IMAPS proxy exception rolled back.\n'
