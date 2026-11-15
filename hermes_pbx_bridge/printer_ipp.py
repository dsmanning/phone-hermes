from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import struct
import sys
from typing import Any, Callable
from urllib.parse import urlsplit


DEFAULT_PRINTER_URI = "ipp://printer.agent.lan:10631/ipp/print"
DEFAULT_PROXY = "http://egress-proxy:3128"


@dataclass(frozen=True, slots=True)
class PrintSettings:
    copies: int
    sides: str
    color_mode: str
    media: str

    def __post_init__(self) -> None:
        if not 1 <= self.copies <= 20:
            raise ValueError("copies must be between 1 and 20")
        if self.sides not in {
            "one-sided",
            "two-sided-long-edge",
            "two-sided-short-edge",
        }:
            raise ValueError("unsupported sides value")
        if self.color_mode not in {"monochrome", "color"}:
            raise ValueError("unsupported color mode")
        if not self.media or len(self.media) > 127:
            raise ValueError("invalid media value")


def parse_proxy_address(proxy_url: str) -> tuple[str, int]:
    parsed = urlsplit(proxy_url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("IPP proxy must be an uncredentialed http:// URL")
    return parsed.hostname, parsed.port or 3128


def create_http_connect_connection(
    address: tuple[str, int],
    timeout: float,
    *,
    proxy_address: tuple[str, int],
    connector: Callable = socket.create_connection,
):
    host, port = address
    sock = connector(proxy_address, timeout)
    request = (
        f"CONNECT {host}:{port} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Proxy-Connection: keep-alive\r\n\r\n"
    ).encode("ascii")
    sock.sendall(request)
    response = bytearray()
    while not response.endswith(b"\r\n\r\n"):
        chunk = sock.recv(1)
        if not chunk:
            sock.close()
            raise ConnectionError("HTTP proxy closed before CONNECT response")
        response.extend(chunk)
        if len(response) > 65536:
            sock.close()
            raise ConnectionError("HTTP proxy returned oversized CONNECT response")
    status_line = bytes(response).split(b"\r\n", 1)[0]
    if not status_line.startswith(b"HTTP/") or b" 200 " not in status_line:
        sock.close()
        raise ConnectionError(
            "HTTP proxy rejected printer CONNECT request: "
            + status_line.decode("latin1", errors="replace")
        )
    return sock


def _attribute(tag: int, name: str, value: bytes) -> bytes:
    encoded_name = name.encode("utf-8")
    return (
        bytes([tag])
        + struct.pack(">H", len(encoded_name))
        + encoded_name
        + struct.pack(">H", len(value))
        + value
    )


def _text_attribute(tag: int, name: str, value: str) -> bytes:
    return _attribute(tag, name, value.encode("utf-8"))


def _base_request(operation_id: int, request_id: int, printer_uri: str) -> bytearray:
    request = bytearray(struct.pack(">BBHI", 2, 0, operation_id, request_id))
    request.append(0x01)  # operation-attributes-tag
    request.extend(_text_attribute(0x47, "attributes-charset", "utf-8"))
    request.extend(_text_attribute(0x48, "attributes-natural-language", "en"))
    request.extend(_text_attribute(0x45, "printer-uri", printer_uri))
    request.extend(_text_attribute(0x42, "requesting-user-name", "hermes"))
    return request


def build_get_printer_attributes(*, request_id: int, printer_uri: str) -> bytes:
    request = _base_request(0x000B, request_id, printer_uri)
    requested = (
        "printer-name",
        "printer-state",
        "printer-state-reasons",
        "printer-is-accepting-jobs",
        "document-format-supported",
        "media-supported",
        "sides-supported",
        "print-color-mode-supported",
    )
    for index, value in enumerate(requested):
        request.extend(_text_attribute(0x44, "requested-attributes" if index == 0 else "", value))
    request.append(0x03)
    return bytes(request)


def validate_pdf(document: bytes) -> None:
    if not document.startswith(b"%PDF-") or b"%%EOF" not in document[-4096:]:
        raise ValueError("document must be a complete PDF")


def build_print_job(
    *,
    request_id: int,
    printer_uri: str,
    job_name: str,
    document: bytes,
    settings: PrintSettings,
) -> bytes:
    validate_pdf(document)
    if not job_name.strip() or len(job_name) > 127:
        raise ValueError("job name must contain 1-127 characters")
    request = _base_request(0x0002, request_id, printer_uri)
    request.extend(_text_attribute(0x42, "job-name", job_name.strip()))
    request.extend(_text_attribute(0x49, "document-format", "application/pdf"))
    request.append(0x02)  # job-attributes-tag
    request.extend(_attribute(0x21, "copies", struct.pack(">I", settings.copies)))
    request.extend(_text_attribute(0x44, "sides", settings.sides))
    request.extend(_text_attribute(0x44, "print-color-mode", settings.color_mode))
    request.extend(_text_attribute(0x44, "media", settings.media))
    request.append(0x03)
    request.extend(document)
    return bytes(request)


def _decode_value(tag: int, value: bytes) -> Any:
    if tag in {0x21, 0x23} and len(value) == 4:
        return struct.unpack(">I", value)[0]
    if tag == 0x22 and len(value) == 1:
        return bool(value[0])
    if tag in {0x41, 0x42, 0x44, 0x45, 0x47, 0x48, 0x49}:
        return value.decode("utf-8", errors="replace")
    return value.hex()


def parse_ipp_response(body: bytes) -> dict[str, Any]:
    if len(body) < 8:
        raise ValueError("IPP response is shorter than its header")
    major, minor, status_code, request_id = struct.unpack(">BBHI", body[:8])
    offset = 8
    attributes: dict[str, Any] = {}
    current_name = ""
    while offset < len(body):
        tag = body[offset]
        offset += 1
        if tag == 0x03:
            break
        if tag <= 0x0F:  # group delimiter
            current_name = ""
            continue
        if offset + 2 > len(body):
            raise ValueError("truncated IPP attribute name length")
        name_length = struct.unpack(">H", body[offset : offset + 2])[0]
        offset += 2
        if offset + name_length + 2 > len(body):
            raise ValueError("truncated IPP attribute name")
        if name_length:
            current_name = body[offset : offset + name_length].decode(
                "utf-8", errors="replace"
            )
        offset += name_length
        value_length = struct.unpack(">H", body[offset : offset + 2])[0]
        offset += 2
        if offset + value_length > len(body):
            raise ValueError("truncated IPP attribute value")
        value = _decode_value(tag, body[offset : offset + value_length])
        offset += value_length
        if not current_name:
            continue
        if current_name in attributes:
            existing = attributes[current_name]
            if not isinstance(existing, list):
                attributes[current_name] = [existing]
            attributes[current_name].append(value)
        else:
            attributes[current_name] = value
    return {
        "version": f"{major}.{minor}",
        "status_code": status_code,
        "request_id": request_id,
        "attributes": attributes,
    }


def send_ipp_request(
    body: bytes,
    *,
    printer_uri: str,
    proxy_url: str | None,
    timeout: float = 30,
) -> dict[str, Any]:
    parsed = urlsplit(printer_uri)
    if parsed.scheme != "ipp" or not parsed.hostname:
        raise ValueError("printer URI must use ipp:// and include a host")
    host = parsed.hostname
    port = parsed.port or 631
    if proxy_url:
        sock = create_http_connect_connection(
            (host, port), timeout, proxy_address=parse_proxy_address(proxy_url)
        )
    else:
        sock = socket.create_connection((host, port), timeout)
    try:
        path = parsed.path or "/"
        request_headers = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Type: application/ipp\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request_headers + body)
        response = http.client.HTTPResponse(sock)
        response.begin()
        response_body = response.read()
        if response.status != 200:
            raise ConnectionError(f"printer returned HTTP {response.status}")
        result = parse_ipp_response(response_body)
        if result["status_code"] > 0x00FF:
            raise RuntimeError(
                f"printer rejected IPP request with status 0x{result['status_code']:04x}"
            )
        return result
    finally:
        sock.close()


def print_confirmation_token(document: bytes, settings: PrintSettings) -> str:
    digest = hashlib.sha256()
    digest.update(document)
    digest.update(
        json.dumps(
            {
                "copies": settings.copies,
                "sides": settings.sides,
                "color_mode": settings.color_mode,
                "media": settings.media,
            },
            sort_keys=True,
        ).encode()
    )
    return "PRINT-" + digest.hexdigest()[:16]


def _settings_from_args(args: argparse.Namespace) -> PrintSettings:
    return PrintSettings(args.copies, args.sides, args.color_mode, args.media)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scoped IPP client for the Dell printer")
    parser.add_argument("--printer-uri", default=DEFAULT_PRINTER_URI)
    parser.add_argument(
        "--proxy",
        default=os.environ.get("IPP_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or DEFAULT_PROXY,
    )
    parser.add_argument("--direct", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")

    for name in ("plan", "print"):
        command = subparsers.add_parser(name)
        command.add_argument("--file", type=Path, required=True)
        command.add_argument("--job-name", required=True)
        command.add_argument("--copies", type=int, required=True)
        command.add_argument(
            "--sides",
            choices=("one-sided", "two-sided-long-edge", "two-sided-short-edge"),
            required=True,
        )
        command.add_argument(
            "--color-mode", choices=("monochrome", "color"), required=True
        )
        command.add_argument("--media", required=True)
        if name == "print":
            command.add_argument("--confirm", required=True)

    args = parser.parse_args(argv)
    proxy_url = None if args.direct else args.proxy
    if args.command == "status":
        result = send_ipp_request(
            build_get_printer_attributes(request_id=1, printer_uri=args.printer_uri),
            printer_uri=args.printer_uri,
            proxy_url=proxy_url,
        )
        print(json.dumps(result, sort_keys=True))
        return 0

    document = args.file.read_bytes()
    validate_pdf(document)
    settings = _settings_from_args(args)
    token = print_confirmation_token(document, settings)
    plan = {
        "file": str(args.file),
        "sha256": hashlib.sha256(document).hexdigest(),
        "bytes": len(document),
        "job_name": args.job_name,
        "copies": settings.copies,
        "sides": settings.sides,
        "color_mode": settings.color_mode,
        "media": settings.media,
        "confirmation": token,
    }
    if args.command == "plan":
        print(json.dumps(plan, sort_keys=True))
        return 0
    if args.confirm != token:
        raise SystemExit("confirmation token does not match the document and settings")
    result = send_ipp_request(
        build_print_job(
            request_id=2,
            printer_uri=args.printer_uri,
            job_name=args.job_name,
            document=document,
            settings=settings,
        ),
        printer_uri=args.printer_uri,
        proxy_url=proxy_url,
        timeout=60,
    )
    print(json.dumps({"plan": plan, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
