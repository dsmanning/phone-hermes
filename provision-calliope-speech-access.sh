#!/usr/bin/env bash
set -euo pipefail

BACKEND_ROOT=${BACKEND_ROOT:-/media/athena/chiron/hermes-output/hermes-dev-backend}
GATEWAY_ROOT=${GATEWAY_ROOT:-/opt/hermes-lan-gateway}
FIREWALL_FILE=${FIREWALL_FILE:-/usr/local/sbin/hermes-agent-lan-firewall}
UNIT_ROOT=${UNIT_ROOT:-/etc/systemd/system}
OUTPUT_SOURCE=${OUTPUT_SOURCE:-/media/athena/chiron/hermes-output}
SOCKET_PROXY_BIN=${SOCKET_PROXY_BIN:-/usr/lib/systemd/systemd-socket-proxyd}
MODE=${1:-}

if [[ -n "$MODE" && "$MODE" != "--prepare-only" ]]; then
    printf 'Usage: %s [--prepare-only]\n' "$0" >&2
    exit 2
fi

COMPOSE_FILE=$BACKEND_ROOT/compose.yaml
SQUID_FILE=$BACKEND_ROOT/squid.conf
DNSMASQ_FILE=$GATEWAY_ROOT/dnsmasq.conf
for path in "$COMPOSE_FILE" "$SQUID_FILE" "$DNSMASQ_FILE" "$FIREWALL_FILE"; do
    [[ -f "$path" ]] || { printf 'Required file is missing: %s\n' "$path" >&2; exit 1; }
done
[[ -d "$OUTPUT_SOURCE" ]] || { printf 'Output directory is missing: %s\n' "$OUTPUT_SOURCE" >&2; exit 1; }
mkdir -p -- "$UNIT_ROOT"

backup_once() {
    local source=$1 backup=$2
    [[ -f "$backup" ]] || cp -p -- "$source" "$backup"
}
backup_once "$COMPOSE_FILE" "$COMPOSE_FILE.before-calliope-speech"
backup_once "$SQUID_FILE" "$SQUID_FILE.before-calliope-speech"
backup_once "$DNSMASQ_FILE" "$DNSMASQ_FILE.before-calliope-speech"
backup_once "$FIREWALL_FILE" "$FIREWALL_FILE.before-calliope-speech"

python3 - "$COMPOSE_FILE" "$SQUID_FILE" "$DNSMASQ_FILE" "$FIREWALL_FILE" "$OUTPUT_SOURCE" <<'PY'
from pathlib import Path
import os
import sys

compose_path, squid_path, dns_path, firewall_path = map(Path, sys.argv[1:5])
output_source = sys.argv[5]


def replace_file(path, content):
    temporary = path.with_name(path.name + ".calliope-speech.tmp")
    temporary.write_text(content)
    os.chmod(temporary, path.stat().st_mode)
    os.replace(temporary, path)


dns = dns_path.read_text()
for name in ("piper.agent.lan", "whisper.agent.lan"):
    line = f"address=/{name}/172.30.55.1\n"
    if line not in dns:
        dns += line
replace_file(dns_path, dns)

firewall = firewall_path.read_text()
drop = '    "$IPT" -A "$CHAIN" -j DROP\n'
if drop not in firewall:
    raise SystemExit("Expected gateway DROP rule is missing")
for port in (10200, 10300):
    line = f'    "$IPT" -A "$CHAIN" -p tcp -d "$GATEWAY" --dport {port} -j ACCEPT\n'
    if line not in firewall:
        firewall = firewall.replace(drop, line + drop, 1)
replace_file(firewall_path, firewall)

squid = squid_path.read_text()
acl_anchor = "acl dev_backend src 172.21.0.10/32\n"
private_deny = "http_access deny private_networks\n"
acls = (
    "acl calliope_speech dstdomain piper.agent.lan whisper.agent.lan\n"
    "acl calliope_speech_ports port 10200 10300\n"
)
allow = "http_access allow dev_backend CONNECT calliope_speech calliope_speech_ports\n"
if acl_anchor not in squid or private_deny not in squid:
    raise SystemExit("Expected Squid policy anchors are missing")
if acls not in squid:
    squid = squid.replace(acl_anchor, acl_anchor + acls, 1)
if allow not in squid:
    squid = squid.replace(private_deny, allow + private_deny, 1)
if "192.168.1.63" in squid:
    raise SystemExit("Squid must target policy names, not Calliope directly")
replace_file(squid_path, squid)

compose = compose_path.read_text()
proxy_anchor = "  egress-proxy:\n"
dev_anchor = "  dev:\n"
policy_network = "      - hermes-agent-lan\n"
if proxy_anchor not in compose or dev_anchor not in compose or policy_network not in compose.split(dev_anchor, 1)[0]:
    raise SystemExit("Expected mediated egress-proxy policy network is missing")
proxy_section, remainder = compose.split(dev_anchor, 1)
extra_hosts = (
    "    extra_hosts:\n"
    '      - "piper.agent.lan:172.30.55.1"\n'
    '      - "whisper.agent.lan:172.30.55.1"\n'
)
if '"piper.agent.lan:172.30.55.1"' not in proxy_section:
    lines = proxy_section.splitlines(keepends=True)
    insertion = next(i for i, line in enumerate(lines) if line.strip() == "networks:")
    lines[insertion:insertion] = [extra_hosts]
    proxy_section = "".join(lines)
compose = proxy_section + dev_anchor + remainder
mount = f"      - {output_source}:/nas-output\n"
if mount not in compose:
    dev_start = compose.index(dev_anchor)
    volumes = compose.find("    volumes:\n", dev_start)
    if volumes < 0:
        raise SystemExit("Expected development-container volumes block is missing")
    first_mount_end = compose.find("\n", volumes + len("    volumes:\n")) + 1
    compose = compose[:first_mount_end] + mount + compose[first_mount_end:]
replace_file(compose_path, compose)
PY

write_unit() {
    local service=$1 port=$2
    cat >"$UNIT_ROOT/hermes-calliope-${service}-proxy.socket" <<EOF
[Unit]
Description=Hermes policy listener for Calliope ${service}

[Socket]
ListenStream=172.30.55.1:${port}
NoDelay=true

[Install]
WantedBy=sockets.target
EOF
    cat >"$UNIT_ROOT/hermes-calliope-${service}-proxy.service" <<EOF
[Unit]
Description=Forward scoped Hermes ${service} traffic to Calliope
After=network-online.target

[Service]
ExecStart=${SOCKET_PROXY_BIN} calliope.home.arpa:${port}
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
NoNewPrivileges=true
EOF
}
write_unit piper 10200
write_unit whisper 10300

printf 'Prepared scoped Calliope speech access.\n'
printf 'Rollback with %s/rollback-calliope-speech-access.sh\n' "$(dirname -- "$0")"
[[ "$MODE" == "--prepare-only" ]] && exit 0

if [[ $(id -u) -ne 0 ]]; then
    printf 'Run without --prepare-only as root so services and containers can be reloaded.\n' >&2
    exit 1
fi
[[ -x "$SOCKET_PROXY_BIN" ]] || { printf 'Missing %s\n' "$SOCKET_PROXY_BIN" >&2; exit 1; }
RUNTIME_ENV=$BACKEND_ROOT/.runtime.env
[[ -f "$RUNTIME_ENV" ]] || { printf 'Runtime environment is missing: %s\n' "$RUNTIME_ENV" >&2; exit 1; }

docker exec hermes-lan-dns dnsmasq --test --conf-file=/etc/dnsmasq.conf
docker restart hermes-lan-dns >/dev/null
systemctl daemon-reload
systemctl enable --now hermes-calliope-piper-proxy.socket hermes-calliope-whisper-proxy.socket
"$FIREWALL_FILE" apply
docker compose --project-directory "$BACKEND_ROOT" --env-file "$RUNTIME_ENV" config --quiet
docker compose --project-directory "$BACKEND_ROOT" --env-file "$RUNTIME_ENV" up -d --no-build --force-recreate egress-proxy dev
docker exec hermes-dev-egress /usr/sbin/squid -k parse -f /etc/squid/squid.conf

if docker inspect hermes-dev --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' | grep -qw hermes-agent-lan; then
    printf 'hermes-dev is unexpectedly attached directly to hermes-agent-lan.\n' >&2
    exit 1
fi

docker exec --user 1000:1000 --workdir /workspace/phone-hermes hermes-dev sh -eu -c '
    test -w /nas-output
    python3 -m hermes_pbx_bridge.speech_cli synthesize \
      --text "Calliope speech route verification." \
      --output /nas-output/.calliope-speech-route-probe.wav
    python3 -c '\''import wave; p="/nas-output/.calliope-speech-route-probe.wav"; w=wave.open(p,"rb"); assert w.getnframes()>0; w.close()'\''
    rm -f -- /nas-output/.calliope-speech-route-probe.wav
'
getent ahostsv4 calliope.home.arpa >/dev/null
systemctl is-active --quiet hermes-calliope-piper-proxy.socket hermes-calliope-whisper-proxy.socket
printf 'Scoped Calliope speech access and live Piper synthesis passed.\n'
