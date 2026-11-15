#!/usr/bin/env bash
set -u

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUTPUT=${OUTPUT:-$ROOT/diagnostics/hermes-dashboard-host.txt}
TARGET_HOST=chiron.local

mkdir -p "$(dirname -- "$OUTPUT")"

if [ "${SKIP_SUDO_PREFLIGHT:-0}" != "1" ] && command -v sudo >/dev/null 2>&1; then
  printf 'Administrative discovery needs sudo. Remain at this terminal for the password prompt.\n'
  sudo -v
fi

section() {
  printf '\n===== %s =====\n' "$1"
}

run() {
  printf '+ '
  printf '%q ' "$@"
  printf '\n'
  "$@" 2>&1 || printf '[command exited %s]\n' "$?"
}

{
  printf 'Hermes dashboard host discovery\n'
  printf 'Target hostname: %s\n' "$TARGET_HOST"
  printf 'Requested endpoint: https://%s:443\n' "$TARGET_HOST"
  printf 'Collected at: '
  date --iso-8601=seconds 2>/dev/null || date

  section 'Execution boundary'
  run hostname
  run id
  run uname -a
  run sh -c 'test -r /etc/os-release && sed -n "1,80p" /etc/os-release'
  run sh -c 'test -r /proc/1/cgroup && sed -n "1,80p" /proc/1/cgroup'
  run sh -c 'test -r /proc/net/route && sed -n "1,120p" /proc/net/route'
  run sh -c 'test -r /etc/resolv.conf && sed -n "1,120p" /etc/resolv.conf'
  if command -v ip >/dev/null 2>&1; then
    run ip -4 address show
    run ip -4 route show
  fi

  section 'Name resolution'
  run getent ahostsv4 chiron.local
  if command -v resolvectl >/dev/null 2>&1; then
    run resolvectl status
    run resolvectl query chiron.local
  fi

  section 'Port ownership'
  if command -v ss >/dev/null 2>&1; then
    run sudo ss -lntup 'sport = :443 or sport = :9119'
  else
    printf 'ss is not installed\n'
  fi

  section 'Hermes installation'
  run sh -c 'command -v hermes || true'
  if command -v hermes >/dev/null 2>&1; then
    run sh -c 'readlink -f "$(command -v hermes)"'
    run sh -c 'file "$(readlink -f "$(command -v hermes)")"'
    run sh -c 'sed -n "1,8p" "$(readlink -f "$(command -v hermes)")"'
    run hermes --version
    run hermes dashboard --help
    run hermes profile list
    run sh -c 'printf "HERMES_HOME=%s\\n" "${HERMES_HOME:-$HOME/.hermes}"'
  fi
  if command -v node >/dev/null 2>&1; then
    run node --version
  fi
  if command -v npm >/dev/null 2>&1; then
    run npm --version
  fi
  if command -v systemctl >/dev/null 2>&1; then
    run systemctl list-unit-files 'hermes*'
    run systemctl list-units --all 'hermes*'
  fi

  section 'Dashboard deployment state'
  if command -v systemctl >/dev/null 2>&1; then
    run systemctl status hermes-dashboard.service --no-pager
    run systemctl status hermes-dashboard-firewall.service --no-pager
    run sudo journalctl --no-pager -n 160 -u hermes-dashboard.service
    run sudo journalctl --no-pager -n 160 -u hermes-dashboard-firewall.service
    run sudo journalctl --no-pager -n 160 -u caddy.service
  fi
  for path in /etc/systemd/system/hermes-dashboard.service /etc/systemd/system/hermes-dashboard-firewall.service /usr/local/sbin/hermes-dashboard-firewall; do
    run sudo stat "$path"
  done
  run sudo sh -c 'printf "%s\n" "--- hermes-dashboard.service ---"; sed -n "1,240p" /etc/systemd/system/hermes-dashboard.service'
  run sudo sh -c 'printf "%s\n" "--- hermes-dashboard-firewall.service ---"; sed -n "1,240p" /etc/systemd/system/hermes-dashboard-firewall.service'
  run sudo sh -c 'printf "%s\n" "--- hermes-dashboard-firewall ---"; sed -n "1,280p" /usr/local/sbin/hermes-dashboard-firewall'
  if command -v curl >/dev/null 2>&1; then
    run curl -4 -sS --connect-timeout 3 --max-time 10 http://127.0.0.1:9119/api/status
    run curl -4 -ksS --resolve chiron.local:443:127.0.0.1 --connect-timeout 3 --max-time 10 -D - -o /dev/null https://chiron.local/
  fi

  section 'System Caddy'
  run sh -c 'command -v caddy || true'
  if command -v caddy >/dev/null 2>&1; then
    run caddy version
    run caddy list-modules
  fi
  if command -v systemctl >/dev/null 2>&1; then
    run systemctl show caddy.service --no-pager -p LoadState -p ActiveState -p FragmentPath -p User -p Group -p ExecStart -p EnvironmentFiles
    run systemctl status caddy.service --no-pager
  fi
  for path in /etc/caddy /etc/caddy/Caddyfile /opt/caddy /opt/hermes-lan-gateway; do
    run sudo stat "$path"
  done
  run sudo sh -c 'printf "%s\n" "--- /etc/caddy/Caddyfile ---"; sed -n "1,240p" /etc/caddy/Caddyfile'
  run sudo sh -c 'printf "%s\n" "--- /etc/caddy files ---"; find /etc/caddy -maxdepth 2 -type f -printf "%p\n" | sort'

  section 'Container Caddy'
  run sh -c 'command -v docker || true'
  if command -v docker >/dev/null 2>&1; then
    run sudo docker ps --format '{{.ID}} {{.Names}} {{.Image}} {{.Ports}} {{.Status}}'
    run sudo docker inspect hermes-lan-gateway --format '{{.Name}} image={{.Config.Image}} state={{.State.Status}} network_mode={{.HostConfig.NetworkMode}} ports={{json .NetworkSettings.Ports}} networks={{json .NetworkSettings.Networks}} mounts={{json .Mounts}}'
    run sudo docker exec hermes-lan-gateway caddy list-modules
  fi
  for path in /opt/hermes-lan-gateway/Caddyfile /opt/hermes-lan-gateway/dnsmasq.conf; do
    run sudo stat "$path"
  done
  run sudo sh -c 'printf "%s\n" "--- /opt/hermes-lan-gateway/Caddyfile ---"; sed -n "1,280p" /opt/hermes-lan-gateway/Caddyfile'
  run sudo sh -c 'printf "%s\n" "--- /opt/hermes-lan-gateway/dnsmasq.conf ---"; sed -n "1,240p" /opt/hermes-lan-gateway/dnsmasq.conf'

  section 'Firewall ownership and policy'
  for path in /usr/local/sbin/hermes-agent-lan-firewall /etc/nftables.conf /etc/ufw; do
    run sudo stat "$path"
  done
  run sudo sh -c 'printf "%s\n" "--- /usr/local/sbin/hermes-agent-lan-firewall ---"; sed -n "1,280p" /usr/local/sbin/hermes-agent-lan-firewall'
  if command -v nft >/dev/null 2>&1; then
    run sudo nft -a list ruleset
  fi
  if [ -x /usr/sbin/iptables-save ]; then
    run sudo /usr/sbin/iptables-save
  elif command -v iptables-save >/dev/null 2>&1; then
    run sudo iptables-save
  fi
  if [ -x /usr/sbin/ufw ]; then
    run sudo /usr/sbin/ufw status verbose
  elif command -v ufw >/dev/null 2>&1; then
    run sudo ufw status verbose
  fi

  section 'Caddy certificate trust material'
  for path in /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt /root/.local/share/caddy/pki/authorities/local/root.crt "$HOME/.local/share/caddy/pki/authorities/local/root.crt"; do
    run sudo stat "$path"
  done

  printf '\nCOMPLETED hermes-dashboard-host discovery\n'
} >"$OUTPUT" 2>&1

printf 'Discovery report written to %s\n' "$OUTPUT"
