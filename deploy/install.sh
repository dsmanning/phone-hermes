#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi

SOURCE_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
HERMES_ENV=${HERMES_ENV:-/home/dsm/.hermes/.env}
INSTALL_ROOT=/opt/hermes-pbx-bridge
SERVICE_ENV=/etc/hermes-pbx-bridge.env
SERVICE_UNIT=/etc/systemd/system/hermes-pbx-bridge.service
PBX_SOURCE=${PBX_SOURCE:-10.1.30.20}
HERMES_API_BASE_URL=${HERMES_API_BASE_URL:-http://127.0.0.1:8642}

for command in python3 systemctl getent install; do
  command -v "$command" >/dev/null || { echo "Required command missing: $command" >&2; exit 1; }
done
[[ -r "$HERMES_ENV" ]] || { echo "Hermes environment file is not readable: $HERMES_ENV" >&2; exit 1; }

API_KEY=$(python3 - "$HERMES_ENV" <<'PY'
from pathlib import Path
import sys

value = ""
for raw in Path(sys.argv[1]).read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[7:].lstrip()
    key, separator, candidate = line.partition("=")
    if separator and key.strip() == "API_SERVER_KEY":
        value = candidate.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        break
if not value:
    raise SystemExit(1)
print(value)
PY
) || {
  echo "API_SERVER_KEY is not configured in $HERMES_ENV" >&2
  echo "Enable Hermes' authenticated API server before installing the bridge." >&2
  exit 1
}

cd "$SOURCE_ROOT"
python3 -m unittest discover -s tests -v

if ! getent passwd hermes-pbx >/dev/null; then
  useradd --system --home-dir "$INSTALL_ROOT" --shell /usr/sbin/nologin hermes-pbx
fi
install -d -o root -g root -m 0755 "$INSTALL_ROOT"
python3 - "$SOURCE_ROOT/hermes_pbx_bridge" "$INSTALL_ROOT/app" <<'PY'
from pathlib import Path
import os
import shutil
import sys

source = Path(sys.argv[1])
target_root = Path(sys.argv[2])
target = target_root / "hermes_pbx_bridge"
shutil.rmtree(target_root, ignore_errors=True)
target_root.mkdir(mode=0o755)
shutil.copytree(source, target)
for path in target_root.rglob("*"):
    os.chown(path, 0, 0)
    path.chmod(0o755 if path.is_dir() else 0o644)
os.chown(target_root, 0, 0)
PY

ENV_TMP=$(mktemp)
trap 'rm -f "$ENV_TMP"' EXIT
{
  printf 'PBX_BRIDGE_BIND=0.0.0.0\n'
  printf 'PBX_BRIDGE_PORT=8443\n'
  printf 'PBX_BRIDGE_ALLOWED_SOURCES=%s\n' "$PBX_SOURCE"
  printf 'HERMES_API_BASE_URL=%s\n' "$HERMES_API_BASE_URL"
  printf 'HERMES_API_KEY=%s\n' "$API_KEY"
  printf 'WYOMING_STT_HOST=calliope.local\n'
  printf 'WYOMING_STT_PORT=10300\n'
  printf 'WYOMING_TTS_HOST=calliope.local\n'
  printf 'WYOMING_TTS_PORT=10200\n'
} >"$ENV_TMP"
install -o root -g hermes-pbx -m 0640 "$ENV_TMP" "$SERVICE_ENV"
install -o root -g root -m 0644 "$SOURCE_ROOT/deploy/hermes-pbx-bridge.service" "$SERVICE_UNIT"

systemctl daemon-reload
systemctl enable hermes-pbx-bridge.service
systemctl restart hermes-pbx-bridge.service
systemctl is-active --quiet hermes-pbx-bridge.service

if command -v curl >/dev/null; then
  if curl -fsS --connect-timeout 3 --max-time 10 \
      -H "Authorization: Bearer $API_KEY" \
      "$HERMES_API_BASE_URL/v1/models" >/dev/null; then
    API_STATUS=reachable
  else
    API_STATUS=unreachable
  fi
else
  API_STATUS=not-tested
fi

LISTENER_READY=false
for attempt in {1..20}; do
  if timeout 1 bash -c 'exec 3<>/dev/tcp/127.0.0.1/8443' 2>/dev/null; then
    LISTENER_READY=true
    break
  fi
  sleep 0.1
done
if [[ "$LISTENER_READY" != true ]]; then
  echo "Bridge service is active but TCP/8443 is not listening." >&2
  systemctl status --no-pager hermes-pbx-bridge.service >&2 || true
  exit 1
fi

if command -v bd >/dev/null && [[ -f "$SOURCE_ROOT/.pbx-bead-id" ]]; then
  ISSUE_ID=$(<"$SOURCE_ROOT/.pbx-bead-id")
  bd comment "$ISSUE_ID" "Installed hermes-pbx-bridge systemd service; unit active; Hermes API status: $API_STATUS" >/dev/null || true
fi

printf 'Installed hermes-pbx-bridge.\n'
printf 'Service: active\n'
printf 'Listener: 0.0.0.0:8443 (application allowlist: %s)\n' "$PBX_SOURCE"
printf 'Hermes API: %s\n' "$API_STATUS"
