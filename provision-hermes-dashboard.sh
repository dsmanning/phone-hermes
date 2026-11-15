#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPORT=${REPORT:-$ROOT/diagnostics/hermes-dashboard-provisioning.txt}
HERMES_HOME=/home/dsm/.hermes
ENV_FILE=$HERMES_HOME/.env
CONFIG_FILE=$HERMES_HOME/config.yaml
BACKUP_ROOT=/var/backups/hermes-dashboard
STATE_DIR=/var/lib/hermes-dashboard
CADDYFILE=/etc/caddy/Caddyfile
DASHBOARD_UNIT=/etc/systemd/system/hermes-dashboard.service
FIREWALL_HELPER=/usr/local/sbin/hermes-dashboard-firewall
FIREWALL_UNIT=/etc/systemd/system/hermes-dashboard-firewall.service
PUBLIC_URL=https://chiron.local
USERNAME=${DASHBOARD_USERNAME:-dsm}

if [ "$(id -u)" -eq 0 ]; then
  printf 'Run this script as dsm, not root; it will request sudo where needed.\n' >&2
  exit 2
fi
if [ "$(id -un)" != dsm ]; then
  printf 'This deployment is scoped to the dsm Hermes installation.\n' >&2
  exit 2
fi
for command in sudo hermes caddy systemctl curl python3 openssl ss; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'Required command is missing: %s\n' "$command" >&2
    exit 1
  }
done

printf 'Hermes dashboard deployment needs sudo. Remain at this terminal for prompts.\n'
sudo -v
umask 077
mkdir -p "$(dirname -- "$REPORT")"

if sudo ss -lnt '( sport = :443 )' | grep -q LISTEN; then
  printf 'TCP/443 is already occupied; refusing to change the host.\n' >&2
  exit 1
fi
if sudo ss -lnt '( sport = :9119 )' | grep -q LISTEN; then
  printf 'TCP/9119 is already occupied; refusing to change the host.\n' >&2
  exit 1
fi

printf 'Dashboard username [%s]: ' "$USERNAME"
IFS= read -r entered_username
if [ -n "$entered_username" ]; then
  USERNAME=$entered_username
fi
case "$USERNAME" in
  *[!A-Za-z0-9._-]*|'')
    printf 'Username must contain only letters, digits, dot, underscore, or hyphen.\n' >&2
    exit 2
    ;;
esac

printf 'Dashboard password: '
IFS= read -r -s PASSWORD
printf '\nRepeat dashboard password: '
IFS= read -r -s PASSWORD_CONFIRM
printf '\n'
if [ -z "$PASSWORD" ] || [ "$PASSWORD" != "$PASSWORD_CONFIRM" ]; then
  unset PASSWORD PASSWORD_CONFIRM
  printf 'Passwords were empty or did not match.\n' >&2
  exit 2
fi

PASSWORD_HASH=$(DASHBOARD_PASSWORD="$PASSWORD" python3 -c '
import base64, hashlib, os, secrets
password = os.environ["DASHBOARD_PASSWORD"].encode("utf-8")
salt = secrets.token_bytes(16)
dk = hashlib.scrypt(password, salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=0)
print("scrypt$16384$8$1$%s$%s" % (
    base64.b64encode(salt).decode(),
    base64.b64encode(dk).decode(),
))
')
unset PASSWORD PASSWORD_CONFIRM
SIGNING_SECRET=$(openssl rand -base64 32)

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP=$BACKUP_ROOT/$TIMESTAMP
sudo install -d -m 0700 -o root -g root "$BACKUP" "$STATE_DIR"

backup_file() {
  source_path=$1
  backup_name=$2
  if sudo test -e "$source_path"; then
    sudo cp -a "$source_path" "$BACKUP/$backup_name"
  else
    sudo touch "$BACKUP/$backup_name.absent"
  fi
}
record_service_state() {
  service=$1
  name=$2
  if systemctl is-active --quiet "$service"; then
    sudo touch "$BACKUP/$name.was-active"
  fi
  if systemctl is-enabled --quiet "$service" 2>/dev/null; then
    sudo touch "$BACKUP/$name.was-enabled"
  fi
}

backup_file "$CADDYFILE" etc-caddy-Caddyfile
backup_file "$DASHBOARD_UNIT" etc-systemd-system-hermes-dashboard.service
backup_file "$FIREWALL_HELPER" usr-local-sbin-hermes-dashboard-firewall
backup_file "$FIREWALL_UNIT" etc-systemd-system-hermes-dashboard-firewall.service
backup_file "$ENV_FILE" home-dsm-hermes-env
backup_file "$CONFIG_FILE" home-dsm-hermes-config.yaml
record_service_state caddy.service caddy
record_service_state hermes-dashboard.service hermes-dashboard
record_service_state hermes-dashboard-firewall.service hermes-dashboard-firewall
printf '%s\n' "$BACKUP" | sudo tee "$STATE_DIR/last-backup" >/dev/null
sudo chmod 600 "$STATE_DIR/last-backup"

STAGE=$(mktemp -d)
cleanup() {
  rm -rf "$STAGE"
  unset PASSWORD_HASH SIGNING_SECRET
}
trap cleanup EXIT

cat >"$STAGE/Caddyfile" <<'CADDY'
{
    admin off
    auto_https disable_redirects
}

https://chiron.local {
    bind 0.0.0.0
    tls internal

    @allowed remote_ip 127.0.0.0/8 192.168.1.0/25 10.1.50.0/24
    handle @allowed {
        reverse_proxy 127.0.0.1:9119
    }

    respond "Forbidden" 403
}
CADDY

cat >"$STAGE/hermes-dashboard.service" <<'UNIT'
[Unit]
Description=Hermes authenticated web dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=dsm
Group=dsm
Environment=HOME=/home/dsm
Environment=HERMES_HOME=/home/dsm/.hermes
EnvironmentFile=/home/dsm/.hermes/.env
WorkingDirectory=/home/dsm
ExecStart=/home/dsm/.local/bin/hermes dashboard --host 127.0.0.1 --port 9119 --no-open
Restart=on-failure
RestartSec=5
RestartPreventExitStatus=78
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

cat >"$STAGE/hermes-dashboard-firewall" <<'FIREWALL'
#!/bin/sh
set -eu
IPT=/usr/sbin/iptables
CHAIN=HERMES_DASHBOARD
IFACE=ens18

apply_rules() {
    "$IPT" -N "$CHAIN" 2>/dev/null || true
    "$IPT" -F "$CHAIN"
    "$IPT" -A "$CHAIN" -s 192.168.1.0/25 -p tcp --dport 443 -j ACCEPT
    "$IPT" -A "$CHAIN" -s 10.1.50.0/24 -p tcp --dport 443 -j ACCEPT
    "$IPT" -A "$CHAIN" -p tcp --dport 443 -j DROP
    "$IPT" -C INPUT -i "$IFACE" -p tcp --dport 443 -j "$CHAIN" 2>/dev/null ||
        "$IPT" -I INPUT 1 -i "$IFACE" -p tcp --dport 443 -j "$CHAIN"
}

remove_rules() {
    "$IPT" -D INPUT -i "$IFACE" -p tcp --dport 443 -j "$CHAIN" 2>/dev/null || true
    "$IPT" -F "$CHAIN" 2>/dev/null || true
    "$IPT" -X "$CHAIN" 2>/dev/null || true
}

case "${1:-}" in
    apply) apply_rules ;;
    remove) remove_rules ;;
    *) printf 'Usage: hermes-dashboard-firewall {apply|remove}\n' >&2; exit 2 ;;
esac
FIREWALL

cat >"$STAGE/hermes-dashboard-firewall.service" <<'UNIT'
[Unit]
Description=Restrict Hermes dashboard HTTPS to trusted LAN and WireGuard sources
After=network-pre.target
Before=caddy.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/hermes-dashboard-firewall apply
ExecStop=/usr/local/sbin/hermes-dashboard-firewall remove
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT

caddy validate --config "$STAGE/Caddyfile" --adapter caddyfile

update_env_file() {
  temp_env=$STAGE/hermes.env
  : >"$temp_env"
  if [ -f "$ENV_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in
        HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH=*|HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=*|HERMES_DASHBOARD_BASIC_AUTH_SECRET=*)
          ;;
        *) printf '%s\n' "$line" >>"$temp_env" ;;
      esac
    done <"$ENV_FILE"
  fi
  printf 'HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH=%s\n' "$PASSWORD_HASH" >>"$temp_env"
  printf 'HERMES_DASHBOARD_BASIC_AUTH_SECRET=%s\n' "$SIGNING_SECRET" >>"$temp_env"
  chmod 600 "$temp_env"
  install -m 0600 "$temp_env" "$ENV_FILE"
}

update_env_file
hermes config set dashboard.public_url "$PUBLIC_URL"
hermes config set dashboard.basic_auth.username "$USERNAME"

sudo install -m 0644 "$STAGE/Caddyfile" "$CADDYFILE"
sudo install -m 0644 "$STAGE/hermes-dashboard.service" "$DASHBOARD_UNIT"
sudo install -m 0755 "$STAGE/hermes-dashboard-firewall" "$FIREWALL_HELPER"
sudo install -m 0644 "$STAGE/hermes-dashboard-firewall.service" "$FIREWALL_UNIT"
sudo caddy validate --config "$CADDYFILE" --adapter caddyfile
sudo systemctl daemon-reload
sudo systemctl enable hermes-dashboard-firewall.service hermes-dashboard.service caddy.service
sudo systemctl restart hermes-dashboard-firewall.service
sudo systemctl restart hermes-dashboard.service

STATUS_FILE=$STAGE/status.json
ready=0
for _attempt in $(seq 1 30); do
  if curl -4 -fsS --connect-timeout 1 --max-time 3 http://127.0.0.1:9119/api/status >"$STATUS_FILE"; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  sudo systemctl status hermes-dashboard.service --no-pager >&2 || true
  printf 'Dashboard did not become ready on loopback. Run rollback-hermes-dashboard.sh.\n' >&2
  exit 1
fi
python3 - "$STATUS_FILE" <<'PY'
import json, sys
status = json.load(open(sys.argv[1], encoding="utf-8"))
if status.get("auth_required") is not True:
    raise SystemExit("dashboard auth gate is not enabled")
if "basic" not in status.get("auth_providers", []):
    raise SystemExit("dashboard basic auth provider is not active")
PY

if sudo ss -lnt '( sport = :9119 )' | grep -qE '0\.0\.0\.0:9119|\[::\]:9119'; then
  printf 'Dashboard unexpectedly exposed port 9119 beyond loopback. Rolling back is required.\n' >&2
  exit 1
fi

sudo systemctl restart caddy.service
HTTPS_HEADERS=$STAGE/https-headers.txt
curl -4 -ksS --resolve chiron.local:443:127.0.0.1 --connect-timeout 3 --max-time 10 \
  -D "$HTTPS_HEADERS" -o /dev/null "$PUBLIC_URL/"
if ! grep -Eq '^HTTP/[0-9.]+ 30[1278]' "$HTTPS_HEADERS"; then
  printf 'HTTPS endpoint did not redirect an unauthenticated request to login.\n' >&2
  exit 1
fi
if ! grep -Eqi '^location: /login' "$HTTPS_HEADERS"; then
  printf 'HTTPS endpoint did not advertise the login page.\n' >&2
  exit 1
fi

CA_SOURCE=/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
for _attempt in $(seq 1 10); do
  sudo test -r "$CA_SOURCE" && break
  sleep 1
done
sudo test -r "$CA_SOURCE"
sudo cp "$CA_SOURCE" "$ROOT/diagnostics/hermes-dashboard-caddy-root.crt"
sudo chown "$(id -u):$(id -g)" "$ROOT/diagnostics/hermes-dashboard-caddy-root.crt"
chmod 0644 "$ROOT/diagnostics/hermes-dashboard-caddy-root.crt"

{
  printf 'Hermes dashboard provisioning\n'
  printf 'Endpoint: %s\n' "$PUBLIC_URL"
  printf 'Dashboard upstream: 127.0.0.1:9119\n'
  printf 'Allowed IPv4 sources: 192.168.1.0/25 10.1.50.0/24\n'
  printf 'Authentication: required, provider basic, username %s\n' "$USERNAME"
  printf 'Caddy internal CA: %s\n' "$ROOT/diagnostics/hermes-dashboard-caddy-root.crt"
  printf 'Backup: %s\n' "$BACKUP"
  printf 'COMPLETED hermes-dashboard provisioning\n'
} >"$REPORT"

printf 'Deployment complete: %s\n' "$PUBLIC_URL"
printf 'Install this CA certificate on clients that do not already trust it:\n  %s\n' "$ROOT/diagnostics/hermes-dashboard-caddy-root.crt"
printf 'Report written to %s\n' "$REPORT"
