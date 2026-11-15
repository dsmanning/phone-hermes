#!/usr/bin/env bash
set -euo pipefail

BACKEND_ROOT=${BACKEND_ROOT:-/media/athena/chiron/hermes-output/hermes-dev-backend}
COMPOSE_FILE=$BACKEND_ROOT/compose.yaml
SQUID_FILE=$BACKEND_ROOT/squid.conf
SQUID_BACKUP=$BACKEND_ROOT/squid.conf.before-homeassistant-access
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

if ! grep -Fq -- '      - hermes-agent-lan' "$COMPOSE_FILE" || \
   ! grep -Fq -- '    name: hermes-agent-lan' "$COMPOSE_FILE"; then
    printf 'The egress proxy must already use the external hermes-agent-lan network.\n' >&2
    exit 1
fi
if [[ ! -f "$SQUID_BACKUP" ]]; then
    cp -p -- "$SQUID_FILE" "$SQUID_BACKUP"
fi

python3 - "$SQUID_FILE" <<'PY'
from pathlib import Path
import os
import sys

path = Path(sys.argv[1])
squid = path.read_text()
acl_anchor = "acl dev_backend src 172.21.0.10/32\n"
policy_acls = (
    "acl homeassistant_gateway dstdomain homeassistant.agent.lan\n"
    "acl homeassistant_http port 80\n"
    "acl homeassistant_method method GET HEAD POST\n"
)
upgrade_policy = (
    "http_upgrade_request_protocols websocket allow homeassistant_gateway\n"
    "http_upgrade_request_protocols OTHER deny all\n"
)
policy_peer = (
    "cache_peer 172.30.55.1 parent 80 0 no-query originserver "
    "name=homeassistant_policy\n"
    "cache_peer_access homeassistant_policy allow homeassistant_gateway\n"
    "cache_peer_access homeassistant_policy deny all\n"
    "never_direct allow homeassistant_gateway\n"
)
policy_allow = (
    "http_access allow dev_backend homeassistant_gateway "
    "homeassistant_http homeassistant_method\n"
)
private_deny = "http_access deny private_networks\n"
markers = (
    "homeassistant_gateway",
    "homeassistant_http",
    "homeassistant_method",
    "homeassistant_policy",
)

if acl_anchor not in squid:
    raise SystemExit("Expected Squid development-backend ACL is missing")
if private_deny not in squid:
    raise SystemExit("Expected Squid private-network deny rule is missing")
if any(marker in squid for marker in markers):
    if not all(block in squid for block in (policy_acls, policy_peer, policy_allow)):
        raise SystemExit("Refusing to modify a partial Home Assistant policy")
else:
    squid = squid.replace(acl_anchor, acl_anchor + policy_acls + policy_peer, 1)
    squid = squid.replace(private_deny, policy_allow + private_deny, 1)

legacy_upgrade = (
    "http_upgrade_request_protocols WebSocket allow homeassistant_gateway\n"
)
if legacy_upgrade in squid:
    squid = squid.replace(legacy_upgrade, upgrade_policy.splitlines(keepends=True)[0], 1)

upgrade_lines = [line for line in upgrade_policy.splitlines() if line in squid]
if upgrade_lines and upgrade_policy not in squid:
    raise SystemExit("Refusing to modify a partial WebSocket upgrade policy")
if upgrade_policy not in squid:
    squid = squid.replace(policy_acls, policy_acls + upgrade_policy, 1)

if "192.168.1.68" in squid:
    raise SystemExit("Squid must route only to the policy gateway, not Home Assistant directly")

temporary = path.with_name(path.name + ".homeassistant-access.tmp")
temporary.write_text(squid)
os.chmod(temporary, path.stat().st_mode)
os.replace(temporary, path)
PY

printf 'Prepared mediated Home Assistant access in %s\n' "$SQUID_FILE"
printf 'Rollback file: %s\n' "$SQUID_BACKUP"

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
sudo docker exec hermes-dev-egress \
    /usr/sbin/squid -k parse -f /etc/squid/squid.conf

if sudo docker inspect hermes-dev \
    --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' \
    | grep -qw hermes-agent-lan; then
    printf 'hermes-dev is unexpectedly attached directly to hermes-agent-lan.\n' >&2
    exit 1
fi

sudo docker exec --user 1000:1000 hermes-dev sh -eu -c '
    body=$(mktemp)
    headers=$(mktemp)
    states=$(mktemp)
    trap "rm -f -- $body $headers $states" EXIT

    curl -4 -fsS --connect-timeout 5 --max-time 20 \
        -D "$headers" http://homeassistant.agent.lan/api/ -o "$body"
    python3 -c '\''import json,sys; assert json.load(open(sys.argv[1])) == {"message": "API running."}'\'' "$body"
    grep -Eiq "^Via: .*Caddy.*, .*hermes-dev-egress" "$headers"

    curl -4 -fsS --connect-timeout 5 --max-time 30 \
        http://homeassistant.agent.lan/api/states -o "$states"
    python3 -c '\''import json,sys; assert isinstance(json.load(open(sys.argv[1])), list)'\'' "$states"

    conversation=$(curl -4 -fsS --connect-timeout 5 --max-time 20 \
        -H "Content-Type: application/json" \
        --data '\''{"text":"hermes-connectivity-probe-7f31-no-action","language":"en"}'\'' \
        http://homeassistant.agent.lan/api/conversation/process)
    python3 -c '\''import json,sys; body=json.loads(sys.argv[1]); assert isinstance(body.get("response"), dict)'\'' "$conversation"

    python3 -c '\''import http.client; c=http.client.HTTPConnection("egress-proxy",3128,timeout=10); c.request("GET","http://homeassistant.agent.lan/api/websocket",headers={"Host":"homeassistant.agent.lan","Connection":"Upgrade","Upgrade":"websocket","Sec-WebSocket-Version":"13","Sec-WebSocket-Key":"dGhlIHNhbXBsZSBub25jZQ=="}); r=c.getresponse(); assert r.status == 101, r.status; c.close()'\''

    root_status=$(curl -4 -sS --connect-timeout 5 --max-time 20 \
        -o /dev/null -w "%{http_code}" http://homeassistant.agent.lan/)
    test "$root_status" = 403
    put_status=$(curl -4 -sS --connect-timeout 5 --max-time 20 \
        -X PUT -o /dev/null -w "%{http_code}" \
        http://homeassistant.agent.lan/api/)
    test "$put_status" = 403

    curl -4 -fsS --connect-timeout 5 --max-time 20 \
        http://frigate.agent.lan/api/version >/dev/null
    curl -4 -fsS --connect-timeout 5 --max-time 20 \
        https://github.com/ >/dev/null
    test ! -e /var/run/docker.sock
    ! command -v sudo >/dev/null 2>&1
'

printf 'Mediated Home Assistant REST and WebSocket access passed.\n'
