#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi

OUTPUT=/media/athena/chiron/hermes-output/hermes-pbx-bridge/diagnostics/latest.txt
ENV_FILE=/etc/hermes-pbx-bridge.env
mkdir -p "$(dirname "$OUTPUT")"

API_KEY=$(python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys
for raw in Path(sys.argv[1]).read_text().splitlines():
    key, separator, value = raw.partition("=")
    if separator and key == "HERMES_API_KEY":
        print(value)
        break
PY
)
API_BASE=$(python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys
for raw in Path(sys.argv[1]).read_text().splitlines():
    key, separator, value = raw.partition("=")
    if separator and key == "HERMES_API_BASE_URL":
        print(value)
        break
PY
)

{
  printf '=== service status ===\n'
  systemctl status --no-pager hermes-pbx-bridge.service || true
  printf '\n=== service journal (last 15 minutes) ===\n'
  journalctl -u hermes-pbx-bridge.service --since '15 minutes ago' --no-pager || true
  printf '\n=== listener ===\n'
  ss -ltnp 'sport = :8443' || true
  printf '\n=== Hermes API models probe ===\n'
  curl -sS --connect-timeout 3 --max-time 15 \
    -o /tmp/hermes-pbx-models-body \
    -w 'HTTP %{http_code}\n' \
    -H "Authorization: Bearer $API_KEY" \
    "$API_BASE/v1/models" || true
  sed -n '1,20p' /tmp/hermes-pbx-models-body 2>/dev/null || true
  rm -f /tmp/hermes-pbx-models-body
  printf '\n=== Calliope Wyoming probes ===\n'
  python3 - <<'PY'
import json
import socket

for host in ("calliope.local", "calliope.home.arpa"):
    try:
        print(f"{host} resolves to {socket.gethostbyname(host)}")
    except OSError as exc:
        print(f"{host} resolution failed: {exc}")

for port in (10200, 10300):
    try:
        with socket.create_connection(("calliope.local", port), timeout=3) as sock:
            sock.sendall(b'{"type":"describe","version":"1.8.0"}\n')
            stream = sock.makefile("rb")
            header = json.loads(stream.readline())
            data_length = int(header.get("data_length", 0))
            data = json.loads(stream.read(data_length)) if data_length else header.get("data", {})
            summary = {name: data.get(name) for name in ("asr", "tts")}
            print(f"calliope.local:{port} open; event={header.get('type')}; voice={json.dumps(summary, separators=(',', ':'))}")
    except Exception as exc:
        print(f"calliope.local:{port} probe failed: {type(exc).__name__}: {exc}")
PY
  printf '\n=== Direct Wyoming transaction probes ===\n'
  PYTHONPATH=/media/athena/chiron/hermes-output/hermes-pbx-bridge python3 - <<'PY'
import time
from hermes_pbx_bridge.audio import pcm16le_to_wav
from hermes_pbx_bridge.wyoming import (
    SocketEventClient,
    WyomingSpeechToTextAdapter,
    WyomingTextToSpeechAdapter,
)

started = time.monotonic()
try:
    stt = WyomingSpeechToTextAdapter(lambda: SocketEventClient("calliope.local", 10300, 30))
    text = stt.transcribe(pcm16le_to_wav(bytes(16000)))
    print(f"STT completed in {time.monotonic() - started:.2f}s; transcript={text!r}")
except Exception as exc:
    print(f"STT failed after {time.monotonic() - started:.2f}s: {type(exc).__name__}: {exc}")

started = time.monotonic()
try:
    tts = WyomingTextToSpeechAdapter(lambda: SocketEventClient("calliope.local", 10200, 30))
    audio = tts.synthesize("Telephone bridge test")
    print(f"TTS completed in {time.monotonic() - started:.2f}s; wave_bytes={len(audio)}")
except Exception as exc:
    print(f"TTS failed after {time.monotonic() - started:.2f}s: {type(exc).__name__}: {exc}")
PY
} >"$OUTPUT" 2>&1

chmod 0644 "$OUTPUT"
printf 'Wrote sanitized diagnostics to %s\n' "$OUTPUT"
