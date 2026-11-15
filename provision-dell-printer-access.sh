#!/usr/bin/env bash
set -euo pipefail

BACKEND_ROOT=${BACKEND_ROOT:-/media/athena/chiron/hermes-output/hermes-dev-backend}
FIREWALL_FILE=${FIREWALL_FILE:-/usr/local/sbin/hermes-agent-lan-firewall}
UNIT_ROOT=${UNIT_ROOT:-/etc/systemd/system}
PRINTER_UPSTREAM=${PRINTER_UPSTREAM:-192.168.1.30}
SOCKET_PROXY_BIN=${SOCKET_PROXY_BIN:-/usr/lib/systemd/systemd-socket-proxyd}
MODE=${1:-}

if [[ -n "$MODE" && "$MODE" != "--prepare-only" ]]; then
    printf 'Usage: %s [--prepare-only]\n' "$0" >&2
    exit 2
fi
if [[ ! "$PRINTER_UPSTREAM" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    printf 'PRINTER_UPSTREAM must be a literal IPv4 address.\n' >&2
    exit 2
fi

COMPOSE_FILE=$BACKEND_ROOT/compose.yaml
SQUID_FILE=$BACKEND_ROOT/squid.conf
for path in "$COMPOSE_FILE" "$SQUID_FILE" "$FIREWALL_FILE"; do
    [[ -f "$path" ]] || { printf 'Required file is missing: %s\n' "$path" >&2; exit 1; }
done
mkdir -p -- "$UNIT_ROOT"

backup_once() {
    local source=$1 backup=$2
    [[ -f "$backup" ]] || cp -p -- "$source" "$backup"
}
backup_once "$COMPOSE_FILE" "$COMPOSE_FILE.before-dell-printer"
backup_once "$SQUID_FILE" "$SQUID_FILE.before-dell-printer"
backup_once "$FIREWALL_FILE" "$FIREWALL_FILE.before-dell-printer"

python3 - "$COMPOSE_FILE" "$SQUID_FILE" "$FIREWALL_FILE" <<'PY'
from pathlib import Path
import os
import sys

compose_path, squid_path, firewall_path = map(Path, sys.argv[1:4])


def replace_file(path, content):
    temporary = path.with_name(path.name + ".dell-printer.tmp")
    temporary.write_text(content)
    os.chmod(temporary, path.stat().st_mode)
    os.replace(temporary, path)


firewall = firewall_path.read_text()
drop = '    "$IPT" -A "$CHAIN" -j DROP\n'
rule = '    "$IPT" -A "$CHAIN" -p tcp -d "$GATEWAY" --dport 10631 -j ACCEPT\n'
if drop not in firewall:
    raise SystemExit("Expected gateway DROP rule is missing")
if rule not in firewall:
    firewall = firewall.replace(drop, rule + drop, 1)
replace_file(firewall_path, firewall)

squid = squid_path.read_text()
acl_anchor = "acl dev_backend src 172.21.0.10/32\n"
private_deny = "http_access deny private_networks\n"
acls = (
    "acl dell_printer dstdomain printer.agent.lan\n"
    "acl dell_printer_ipp port 10631\n"
)
allow = "http_access allow dev_backend CONNECT dell_printer dell_printer_ipp\n"
if acl_anchor not in squid or private_deny not in squid:
    raise SystemExit("Expected Squid policy anchors are missing")
if acls not in squid:
    squid = squid.replace(acl_anchor, acl_anchor + acls, 1)
if allow not in squid:
    squid = squid.replace(private_deny, allow + private_deny, 1)
if "192.168.1.30" in squid:
    raise SystemExit("Squid must target the policy name, not the printer directly")
replace_file(squid_path, squid)

compose = compose_path.read_text()
proxy_anchor = "  egress-proxy:\n"
dev_anchor = "  dev:\n"
policy_network = "      - hermes-agent-lan\n"
if proxy_anchor not in compose or dev_anchor not in compose or policy_network not in compose.split(dev_anchor, 1)[0]:
    raise SystemExit("Expected mediated egress-proxy policy network is missing")
proxy_section, remainder = compose.split(dev_anchor, 1)
printer_host = '      - "printer.agent.lan:172.30.55.1"\n'
if printer_host not in proxy_section:
    lines = proxy_section.splitlines(keepends=True)
    extra_hosts_index = next(
        (i for i, line in enumerate(lines) if line.strip() == "extra_hosts:"), None
    )
    if extra_hosts_index is None:
        networks_index = next(i for i, line in enumerate(lines) if line.strip() == "networks:")
        lines[networks_index:networks_index] = ["    extra_hosts:\n", printer_host]
    else:
        insert_at = extra_hosts_index + 1
        while insert_at < len(lines) and lines[insert_at].startswith("      - "):
            insert_at += 1
        lines.insert(insert_at, printer_host)
    proxy_section = "".join(lines)
replace_file(compose_path, proxy_section + dev_anchor + remainder)
PY

cat >"$UNIT_ROOT/hermes-dell-printer-proxy.socket" <<'EOF'
[Unit]
Description=Hermes policy listener for Dell C3765dnf IPP

[Socket]
ListenStream=172.30.55.1:10631
NoDelay=true

[Install]
WantedBy=sockets.target
EOF
cat >"$UNIT_ROOT/hermes-dell-printer-proxy.service" <<EOF
[Unit]
Description=Forward scoped Hermes IPP traffic to Dell C3765dnf
After=network-online.target

[Service]
ExecStart=${SOCKET_PROXY_BIN} ${PRINTER_UPSTREAM}:631
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
NoNewPrivileges=true
EOF

printf 'Prepared scoped Dell printer access.\n'
printf 'Rollback with %s/rollback-dell-printer-access.sh\n' "$(dirname -- "$0")"
[[ "$MODE" == "--prepare-only" ]] && exit 0

if [[ $(id -u) -ne 0 ]]; then
    printf 'Run without --prepare-only as root so services and containers can be reloaded.\n' >&2
    exit 1
fi
[[ -x "$SOCKET_PROXY_BIN" ]] || { printf 'Missing %s\n' "$SOCKET_PROXY_BIN" >&2; exit 1; }
RUNTIME_ENV=$BACKEND_ROOT/.runtime.env
[[ -f "$RUNTIME_ENV" ]] || { printf 'Runtime environment is missing: %s\n' "$RUNTIME_ENV" >&2; exit 1; }

rollback_on_error() {
    local status=$?
    trap - ERR
    printf 'Printer provisioning failed; applying rollback.\n' >&2
    BACKEND_ROOT="$BACKEND_ROOT" FIREWALL_FILE="$FIREWALL_FILE" UNIT_ROOT="$UNIT_ROOT" \
        "$(dirname -- "$0")/rollback-dell-printer-access.sh" || true
    exit "$status"
}
trap rollback_on_error ERR

python3 - "$PRINTER_UPSTREAM" <<'PY'
import socket
import sys
with socket.create_connection((sys.argv[1], 631), timeout=5):
    pass
print("Direct printer IPP listener reachable from Chiron.")
PY
systemctl daemon-reload
systemctl enable --now hermes-dell-printer-proxy.socket
"$FIREWALL_FILE" apply
docker compose --project-directory "$BACKEND_ROOT" --env-file "$RUNTIME_ENV" config --quiet
docker compose --project-directory "$BACKEND_ROOT" --env-file "$RUNTIME_ENV" build egress-proxy
docker compose --project-directory "$BACKEND_ROOT" --env-file "$RUNTIME_ENV" up -d --no-build --force-recreate egress-proxy dev
docker exec hermes-dev-egress /usr/sbin/squid -k parse -f /etc/squid/squid.conf
if docker inspect hermes-dev --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' | grep -qw hermes-agent-lan; then
    printf 'hermes-dev is unexpectedly attached directly to hermes-agent-lan.\n' >&2
    exit 1
fi
docker exec --user 1000:1000 --workdir /workspace/phone-hermes hermes-dev \
    python3 -m hermes_pbx_bridge.printer_ipp status
trap - ERR
printf 'Scoped Dell printer IPP access and attribute query passed.\n'
