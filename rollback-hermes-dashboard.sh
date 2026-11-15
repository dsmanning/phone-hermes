#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPORT=${REPORT:-$ROOT/diagnostics/hermes-dashboard-rollback.txt}
STATE_DIR=/var/lib/hermes-dashboard
POINTER=$STATE_DIR/last-backup

if [ "$(id -u)" -eq 0 ]; then
  printf 'Run this script as dsm, not root; it will request sudo where needed.\n' >&2
  exit 2
fi
printf 'Hermes dashboard rollback needs sudo. Remain at this terminal for prompts.\n'
sudo -v
sudo test -r "$POINTER" || {
  printf 'No rollback pointer exists at %s\n' "$POINTER" >&2
  exit 1
}
BACKUP=$(sudo cat "$POINTER")
case "$BACKUP" in
  /var/backups/hermes-dashboard/*) ;;
  *) printf 'Unsafe backup path in rollback pointer: %s\n' "$BACKUP" >&2; exit 1 ;;
esac
sudo test -d "$BACKUP"

sudo systemctl stop caddy.service 2>/dev/null || true
sudo systemctl stop hermes-dashboard.service 2>/dev/null || true
if sudo test -x /usr/local/sbin/hermes-dashboard-firewall; then
  sudo /usr/local/sbin/hermes-dashboard-firewall remove || true
fi
sudo systemctl stop hermes-dashboard-firewall.service 2>/dev/null || true
sudo systemctl disable hermes-dashboard.service hermes-dashboard-firewall.service 2>/dev/null || true

restore_file() {
  target=$1
  backup_name=$2
  if sudo test -e "$BACKUP/$backup_name.absent"; then
    sudo rm -f "$target"
  elif sudo test -e "$BACKUP/$backup_name"; then
    sudo cp -a "$BACKUP/$backup_name" "$target"
  else
    printf 'Backup component missing: %s\n' "$backup_name" >&2
    exit 1
  fi
}

restore_file /etc/caddy/Caddyfile etc-caddy-Caddyfile
restore_file /etc/systemd/system/hermes-dashboard.service etc-systemd-system-hermes-dashboard.service
restore_file /usr/local/sbin/hermes-dashboard-firewall usr-local-sbin-hermes-dashboard-firewall
restore_file /etc/systemd/system/hermes-dashboard-firewall.service etc-systemd-system-hermes-dashboard-firewall.service
restore_file /home/dsm/.hermes/.env home-dsm-hermes-env
restore_file /home/dsm/.hermes/config.yaml home-dsm-hermes-config.yaml

sudo systemctl daemon-reload
if sudo test -e "$BACKUP/caddy.was-enabled"; then
  sudo systemctl enable caddy.service
else
  sudo systemctl disable caddy.service 2>/dev/null || true
fi
if sudo test -e "$BACKUP/caddy.was-active"; then
  sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  sudo systemctl start caddy.service
fi
if sudo test -e "$BACKUP/hermes-dashboard.was-enabled"; then
  sudo systemctl enable hermes-dashboard.service
fi
if sudo test -e "$BACKUP/hermes-dashboard.was-active"; then
  sudo systemctl start hermes-dashboard.service
fi
if sudo test -e "$BACKUP/hermes-dashboard-firewall.was-enabled"; then
  sudo systemctl enable hermes-dashboard-firewall.service
fi
if sudo test -e "$BACKUP/hermes-dashboard-firewall.was-active"; then
  sudo systemctl start hermes-dashboard-firewall.service
fi

mkdir -p "$(dirname -- "$REPORT")"
{
  printf 'Hermes dashboard rollback\n'
  printf 'Restored backup: %s\n' "$BACKUP"
  printf 'COMPLETED hermes-dashboard rollback\n'
} >"$REPORT"
printf 'Rollback complete. Report written to %s\n' "$REPORT"
