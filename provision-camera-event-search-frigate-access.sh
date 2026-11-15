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
for path in "$COMPOSE_FILE" "$SQUID_FILE"; do
    if [[ ! -f "$path" ]]; then
        printf 'Required file is missing: %s\n' "$path" >&2
        exit 1
    fi
done

if [[ ! -f "$COMPOSE_BACKUP" ]]; then
    cp -p -- "$COMPOSE_FILE" "$COMPOSE_BACKUP"
fi
if [[ ! -f "$SQUID_BACKUP" ]]; then
    cp -p -- "$SQUID_FILE" "$SQUID_BACKUP"
fi

python3 - "$COMPOSE_FILE" "$SQUID_FILE" <<'PY'
from pathlib import Path
import os
import sys

compose_path = Path(sys.argv[1])
squid_path = Path(sys.argv[2])

compose = compose_path.read_text()
proxy_network_anchor = "      - egress\n"
policy_network_line = "      - hermes-agent-lan\n"
dns_block = "    dns:\n      - 172.30.55.1\n      - 127.0.0.11\n"
external_network = (
    "  hermes-agent-lan:\n"
    "    external: true\n"
    "    name: hermes-agent-lan\n"
)

if proxy_network_anchor not in compose:
    raise SystemExit("Expected egress-proxy network anchor is missing")
if policy_network_line not in compose:
    compose = compose.replace(
        proxy_network_anchor,
        proxy_network_anchor + policy_network_line,
        1,
    )
compose = compose.replace(dns_block, "")
if external_network not in compose:
    if not compose.endswith("\n"):
        compose += "\n"
    compose += external_network

squid = squid_path.read_text()
acl_anchor = "acl dev_backend src 172.21.0.10/32\n"
policy_acls = (
    "acl frigate_gateway dstdomain frigate.agent.lan\n"
    "acl frigate_http port 80\n"
    "acl read_method method GET HEAD\n"
)
policy_allow = (
    "http_access allow dev_backend frigate_gateway frigate_http read_method\n"
)
policy_peer = (
    "cache_peer 172.30.55.1 parent 80 0 no-query originserver "
    "name=frigate_policy\n"
    "cache_peer_access frigate_policy allow frigate_gateway\n"
    "cache_peer_access frigate_policy deny all\n"
    "never_direct allow frigate_gateway\n"
)
private_deny = "http_access deny private_networks\n"

if acl_anchor not in squid:
    raise SystemExit("Expected Squid development-backend ACL is missing")
if private_deny not in squid:
    raise SystemExit("Expected Squid private-network deny rule is missing")
if policy_acls not in squid:
    squid = squid.replace(acl_anchor, acl_anchor + policy_acls, 1)
if policy_peer not in squid:
    squid = squid.replace(policy_acls, policy_acls + policy_peer, 1)
if policy_allow not in squid:
    squid = squid.replace(private_deny, policy_allow + private_deny, 1)

for path, content in ((compose_path, compose), (squid_path, squid)):
    temporary = path.with_name(path.name + ".frigate-access.tmp")
    temporary.write_text(content)
    os.chmod(temporary, path.stat().st_mode)
    os.replace(temporary, path)
PY

printf 'Prepared mediated Frigate access in %s and %s\n' "$COMPOSE_FILE" "$SQUID_FILE"
printf 'Rollback files: %s and %s\n' "$COMPOSE_BACKUP" "$SQUID_BACKUP"

if [[ "$MODE" == "--prepare-only" ]]; then
    exit 0
fi
if [[ ! -f "$RUNTIME_ENV" ]]; then
    printf 'Runtime environment is missing: %s\n' "$RUNTIME_ENV" >&2
    exit 1
fi

network_internal=$(sudo docker network inspect hermes-agent-lan --format '{{.Internal}}')
network_ipv6=$(sudo docker network inspect hermes-agent-lan --format '{{.EnableIPv6}}')
if [[ "$network_internal" != "true" || "$network_ipv6" != "false" ]]; then
    printf 'hermes-agent-lan must be internal and IPv4-only.\n' >&2
    exit 1
fi

sudo docker compose --project-directory "$BACKEND_ROOT" \
    --env-file "$RUNTIME_ENV" config --quiet
sudo docker compose --project-directory "$BACKEND_ROOT" \
    --env-file "$RUNTIME_ENV" up -d --build --force-recreate egress-proxy

if sudo docker inspect hermes-dev \
    --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' \
    | grep -qw hermes-agent-lan; then
    printf 'hermes-dev is unexpectedly attached directly to hermes-agent-lan.\n' >&2
    exit 1
fi

sudo docker exec --user 1000:1000 hermes-dev sh -eu -c '
    getent hosts egress-proxy >/dev/null
    body=$(mktemp)
    headers=$(mktemp)
    trap "rm -f -- $body $headers" EXIT
    curl -4 -fsS --connect-timeout 5 --max-time 20 \
        -D "$headers" http://frigate.agent.lan/api/version -o "$body"
    test -s "$body"
    grep -Eiq "^Via: 1\\.1 Caddy" "$headers"
    status=$(curl -4 -sS --connect-timeout 5 --max-time 20 \
        -o /dev/null -w "%{http_code}" http://frigate.agent.lan/)
    test "$status" = 403
    post_status=$(curl -4 -sS --connect-timeout 5 --max-time 20 \
        -X POST -o /dev/null -w "%{http_code}" \
        http://frigate.agent.lan/api/version)
    test "$post_status" = 403
    curl -4 -fsS --connect-timeout 5 --max-time 20 \
        https://github.com/ >/dev/null
    test -w /workspace/camera-event-search
    test -r /workspace/camera-event-search/media
    test ! -w /workspace/camera-event-search/media
    test ! -e /var/run/docker.sock
    ! command -v sudo >/dev/null 2>&1
'

printf 'Mediated read-only Frigate gateway access passed.\n'
