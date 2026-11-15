#!/usr/bin/env bash
set -euo pipefail

BACKEND_ROOT=${BACKEND_ROOT:-/media/athena/chiron/hermes-output/hermes-dev-backend}
GATEWAY_ROOT=${GATEWAY_ROOT:-/opt/hermes-lan-gateway}
FIREWALL_FILE=${FIREWALL_FILE:-/usr/local/sbin/hermes-agent-lan-firewall}
UNIT_ROOT=${UNIT_ROOT:-/etc/systemd/system}
MODE=${1:-}

if [[ -n "$MODE" && "$MODE" != "--prepare-only" ]]; then
    printf 'Usage: %s [--prepare-only]\n' "$0" >&2
    exit 2
fi

restore() {
    local target=$1 backup=$2
    [[ -f "$backup" ]] || { printf 'Rollback file is missing: %s\n' "$backup" >&2; exit 1; }
    cp -fp -- "$backup" "$target"
}
restore "$BACKEND_ROOT/compose.yaml" "$BACKEND_ROOT/compose.yaml.before-calliope-speech"
restore "$BACKEND_ROOT/squid.conf" "$BACKEND_ROOT/squid.conf.before-calliope-speech"
restore "$GATEWAY_ROOT/dnsmasq.conf" "$GATEWAY_ROOT/dnsmasq.conf.before-calliope-speech"
restore "$FIREWALL_FILE" "$FIREWALL_FILE.before-calliope-speech"
rm -f -- \
    "$UNIT_ROOT/hermes-calliope-piper-proxy.socket" \
    "$UNIT_ROOT/hermes-calliope-piper-proxy.service" \
    "$UNIT_ROOT/hermes-calliope-whisper-proxy.socket" \
    "$UNIT_ROOT/hermes-calliope-whisper-proxy.service"
printf 'Restored pre-Calliope speech policy configuration.\n'
[[ "$MODE" == "--prepare-only" ]] && exit 0

if [[ $(id -u) -ne 0 ]]; then
    printf 'Run without --prepare-only as root so services and containers can be reloaded.\n' >&2
    exit 1
fi
RUNTIME_ENV=$BACKEND_ROOT/.runtime.env
[[ -f "$RUNTIME_ENV" ]] || { printf 'Runtime environment is missing: %s\n' "$RUNTIME_ENV" >&2; exit 1; }

systemctl disable --now hermes-calliope-piper-proxy.socket hermes-calliope-whisper-proxy.socket 2>/dev/null || true
systemctl daemon-reload
docker exec hermes-lan-dns dnsmasq --test --conf-file=/etc/dnsmasq.conf
docker restart hermes-lan-dns >/dev/null
"$FIREWALL_FILE" apply
docker compose --project-directory "$BACKEND_ROOT" --env-file "$RUNTIME_ENV" config --quiet
docker compose --project-directory "$BACKEND_ROOT" --env-file "$RUNTIME_ENV" up -d --build --force-recreate egress-proxy dev
docker exec hermes-dev-egress /usr/sbin/squid -k parse -f /etc/squid/squid.conf
printf 'Calliope speech policy rollback applied.\n'
