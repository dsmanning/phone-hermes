#!/usr/bin/env python3
import json
import socket
import time


def read_exact(reader, length):
    started = time.monotonic()
    data = bytearray()
    while len(data) < length:
        chunk = reader.read(length - len(data))
        if not chunk:
            raise EOFError(f"closed after {len(data)}/{length} bytes")
        data.extend(chunk)
    print(f"  body {len(data)} bytes in {time.monotonic() - started:.3f}s", flush=True)
    return bytes(data)


with socket.create_connection(("calliope.local", 10200), 3) as sock:
    sock.settimeout(10)
    request = {
        "type": "synthesize",
        "version": "1.8.0",
        "data": {"text": "Telephone payload trace"},
    }
    wire = json.dumps(request, separators=(",", ":")).encode() + b"\n"
    print(f"sending {len(wire)} request bytes", flush=True)
    sock.sendall(wire)
    reader = sock.makefile("rb")
    for event_number in range(20):
        print(f"event {event_number}: waiting for header", flush=True)
        line = reader.readline()
        if not line:
            raise EOFError("connection closed before header")
        header = json.loads(line)
        print(f"  header={header}", flush=True)
        data_length = int(header.get("data_length", 0))
        if data_length:
            data = json.loads(read_exact(reader, data_length))
            print(f"  data={data}", flush=True)
        payload_length = int(header.get("payload_length", 0))
        if payload_length:
            read_exact(reader, payload_length)
        if header.get("type") == "audio-stop":
            print("PASS: received audio-stop", flush=True)
            break
    else:
        raise RuntimeError("audio-stop not received within 20 events")
