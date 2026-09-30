# Hermes PBX Bridge

An IPv4-only Asterisk AudioSocket bridge that turns an internal telephone extension into a spoken Hermes Agent conversation.

## Architecture

```text
Telephone
  -> Asterisk/FreePBX AudioSocket
  -> hermes-pbx-bridge on TCP/8443
  -> Wyoming Faster Whisper speech-to-text
  -> Hermes /v1/responses
  -> Wyoming Piper text-to-speech
  -> 8 kHz signed-linear AudioSocket audio
  -> Telephone
```

Each call has independent Hermes conversation state through `previous_response_id`. State is discarded when the call ends.

## Trust boundary

- Only explicitly configured PBX source addresses may open AudioSocket calls.
- The first AudioSocket frame must contain a valid, non-nil 16-byte call UUID.
- The PBX reaches only the bridge; it does not receive direct access to Hermes or the speech services.
- Hermes API credentials are read from a root-owned service environment file and are never logged.
- The systemd service runs as a dedicated unprivileged account with filesystem, device, namespace, and capability restrictions.
- The listener is IPv4-only.

## Runtime configuration

`Config.from_environment` accepts:

```text
PBX_BRIDGE_BIND=0.0.0.0
PBX_BRIDGE_PORT=8443
PBX_BRIDGE_ALLOWED_SOURCES=10.1.30.20
HERMES_API_BASE_URL=http://127.0.0.1:8642
HERMES_API_KEY=...
WYOMING_STT_HOST=calliope.local
WYOMING_STT_PORT=10300
WYOMING_TTS_HOST=calliope.local
WYOMING_TTS_PORT=10200
```

`HERMES_API_KEY` and `PBX_BRIDGE_ALLOWED_SOURCES` are required. Do not commit a populated environment file.

## Audio behavior

- Input: AudioSocket `0x10` frames containing 8 kHz, 16-bit, mono PCM.
- Turn detection: RMS threshold with minimum speech, trailing silence, and maximum-turn limits.
- STT: input PCM is wrapped as WAV and sent over Wyoming.
- TTS: Wyoming WAV output is converted to 8 kHz, 16-bit, mono PCM.
- Output: audio is returned in 320-byte AudioSocket frames.

## Tests

The project has no third-party Python runtime dependencies.

```bash
python3 -m unittest discover -s tests -v
```

The suite covers AudioSocket framing, source and UUID validation, call-session isolation, turn detection, audio conversion, Hermes adapters, Wyoming framing, application wiring, and deployment behavior.

## Deployment

The deployment script:

1. Runs the complete test suite.
2. Installs the package under `/opt/hermes-pbx-bridge/app`.
3. Creates the dedicated `hermes-pbx` service account if needed.
4. Reads `API_SERVER_KEY` from the Hermes environment file.
5. Writes a root-owned service environment file.
6. Installs and restarts the hardened systemd unit.
7. Verifies the service and IPv4 TCP/8443 listener.

```bash
sudo deploy/install.sh
```

Defaults can be overridden when needed:

```bash
sudo PBX_SOURCE=10.1.30.20 \
  HERMES_API_BASE_URL=http://127.0.0.1:8642 \
  HERMES_ENV=/home/dsm/.hermes/.env \
  deploy/install.sh
```

## Diagnostics

`deploy/diagnose.sh` writes a sanitized report to `diagnostics/latest.txt`. Runtime diagnostics are excluded from Git because they contain host-specific topology and service details.

```bash
sudo deploy/diagnose.sh
```

The report checks the bridge service and listener, Hermes API reachability, Wyoming discovery, and complete STT/TTS transactions.

`deploy/trace_wyoming_tts.py` is a bounded event-level Wyoming TTS tracer for diagnosing framing or payload-transfer failures.

## Network troubleshooting

A TCP connection or successful Wyoming `describe` event does not prove audio payloads work. Verify a complete STT and TTS transaction.

If event headers arrive but binary audio payloads stall, compare the physical interface MTU with the selected route MTU on both hosts:

```bash
ip -o link show
ip -4 route get <peer-address>
```

A DHCP-provided jumbo route MTU on a 1,500-byte interface can black-hole Wyoming audio payloads and larger SSH key-exchange packets. Correct the network/DHCP configuration rather than adding application retries or widening firewall access.

## Asterisk integration

Configure the PBX dialplan to open an AudioSocket connection to the bridge and provide a unique call UUID. Keep the bridge source allowlist synchronized with the PBX address. The exact FreePBX custom-destination procedure is deployment-specific and intentionally not automated by this repository.
