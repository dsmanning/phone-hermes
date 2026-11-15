#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPORT=${REPORT:-$ROOT/diagnostics/hermes-dashboard-caddy-repair.txt}
PUBLIC_URL=https://chiron.local
CADDYFILE=/etc/caddy/Caddyfile
ROOT_CA=/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
CA_COPY=$ROOT/diagnostics/hermes-dashboard-caddy-root.crt
KNOWN_ERROR='listen tcp 127.0.0.1:2019: bind: address already in use'

if [ "$(id -u)" -eq 0 ]; then
  printf 'Run this script as dsm, not root; it will request sudo where needed.\n' >&2
  exit 2
fi
for command in sudo caddy systemctl curl python3 ss; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'Required command is missing: %s\n' "$command" >&2
    exit 1
  }
done

printf 'Caddy repair needs sudo. Remain at this terminal for the password prompt.\n'
sudo -v

if ! sudo journalctl --no-pager -n 80 -u caddy.service | grep -Fq "$KNOWN_ERROR"; then
  printf 'The expected Caddy admin-listener conflict was not found; refusing an ungrounded repair.\n' >&2
  exit 1
fi
if ! systemctl is-active --quiet hermes-dashboard.service; then
  printf 'Hermes dashboard is not running; refusing to expose an unavailable upstream.\n' >&2
  exit 1
fi

STAGE=$(mktemp -d)
cleanup() {
  rm -rf "$STAGE"
}
trap cleanup EXIT
mkdir -p "$(dirname -- "$REPORT")"

curl -4 -fsS --connect-timeout 3 --max-time 10 http://127.0.0.1:9119/api/status >"$STAGE/status.json"
python3 - "$STAGE/status.json" <<'PY'
import json, sys
status = json.load(open(sys.argv[1], encoding="utf-8"))
if status.get("auth_required") is not True:
    raise SystemExit("dashboard auth gate is not enabled")
if "basic" not in status.get("auth_providers", []):
    raise SystemExit("dashboard basic auth provider is not active")
PY

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

caddy fmt --overwrite "$STAGE/Caddyfile"
caddy validate --config "$STAGE/Caddyfile" --adapter caddyfile

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP=/var/backups/hermes-dashboard-caddy-repair/$TIMESTAMP
sudo install -d -m 0700 -o root -g root "$BACKUP"
sudo cp -a "$CADDYFILE" "$BACKUP/Caddyfile"
sudo install -m 0644 "$STAGE/Caddyfile" "$CADDYFILE"
sudo caddy validate --config "$CADDYFILE" --adapter caddyfile
sudo systemctl restart caddy.service

if ! systemctl is-active --quiet caddy.service; then
  sudo systemctl status caddy.service --no-pager >&2 || true
  printf 'Caddy did not start; restore %s/Caddyfile before further changes.\n' "$BACKUP" >&2
  exit 1
fi

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

for _attempt in $(seq 1 10); do
  sudo test -r "$ROOT_CA" && break
  sleep 1
done
sudo test -r "$ROOT_CA"
sudo cp "$ROOT_CA" "$CA_COPY"
sudo chown "$(id -u):$(id -g)" "$CA_COPY"
chmod 0644 "$CA_COPY"

if sudo ss -lnt '( sport = :9119 )' | grep -qE '0\.0\.0\.0:9119|\[::\]:9119'; then
  printf 'Dashboard port 9119 is exposed beyond loopback.\n' >&2
  exit 1
fi
if ! sudo ss -lnt '( sport = :443 )' | grep -q LISTEN; then
  printf 'Caddy is active but TCP/443 is not listening.\n' >&2
  exit 1
fi

{
  printf 'Hermes dashboard Caddy repair\n'
  printf 'Root cause: %s\n' "$KNOWN_ERROR"
  printf 'Resolution: disabled the second Caddy admin endpoint; HTTPS admin API is not required.\n'
  printf 'Endpoint: %s\n' "$PUBLIC_URL"
  printf 'Authentication: required, provider basic\n'
  printf 'Caddy internal CA: %s\n' "$CA_COPY"
  printf 'Repair backup: %s\n' "$BACKUP"
  printf 'COMPLETED hermes-dashboard Caddy repair\n'
} >"$REPORT"

printf 'Caddy repair completed. Open %s after trusting:\n  %s\n' "$PUBLIC_URL" "$CA_COPY"
printf 'Report written to %s\n' "$REPORT"
