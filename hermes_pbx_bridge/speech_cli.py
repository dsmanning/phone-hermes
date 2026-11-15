import argparse
from functools import partial
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
from urllib.parse import urlsplit
import wave

from .wyoming import (
    SocketEventClient,
    WyomingSpeechToTextAdapter,
    WyomingTextToSpeechAdapter,
    create_http_connect_connection,
)


def parse_proxy_address(proxy_url: str) -> tuple[str, int]:
    parsed = urlsplit(proxy_url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Wyoming proxy must be an uncredentialed http:// URL")
    return parsed.hostname, parsed.port or 3128


def _validate_wav(audio: bytes) -> None:
    with wave.open(io.BytesIO(audio), "rb") as wav_file:
        if wav_file.getnframes() < 1:
            raise ValueError("WAV file contains no audio frames")
        wav_file.readframes(wav_file.getnframes())


def transcribe_path(input_path: Path, adapter) -> str:
    audio = Path(input_path).read_bytes()
    _validate_wav(audio)
    return adapter.transcribe(audio)


def synthesize_to_path(text: str, output_path: Path, adapter) -> None:
    audio = adapter.synthesize(text)
    _validate_wav(audio)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".partial", dir=output_path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(audio)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, output_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _client_factory(host: str, port: int, timeout: float, proxy_url: str | None):
    connector = socket.create_connection
    if proxy_url:
        connector = partial(
            create_http_connect_connection,
            proxy_address=parse_proxy_address(proxy_url),
        )
    return lambda: SocketEventClient(host, port, timeout, connector=connector)


def main(argv=None, environ=None, stdout=None) -> int:
    environment = os.environ if environ is None else environ
    output_stream = sys.stdout if stdout is None else stdout
    parser = argparse.ArgumentParser(
        description="Use Calliope Wyoming speech services through a scoped proxy."
    )
    parser.add_argument(
        "--proxy",
        default=environment.get("WYOMING_PROXY")
        or environment.get("HTTP_PROXY")
        or environment.get("http_proxy"),
    )
    parser.add_argument("--direct", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    synthesize = subparsers.add_parser("synthesize")
    synthesize.add_argument("--text", required=True)
    synthesize.add_argument("--output", type=Path, required=True)
    synthesize.add_argument(
        "--host", default=environment.get("WYOMING_TTS_HOST", "piper.agent.lan")
    )
    synthesize.add_argument(
        "--port", type=int, default=int(environment.get("WYOMING_TTS_PORT", "10200"))
    )

    transcribe = subparsers.add_parser("transcribe")
    transcribe.add_argument("--input", type=Path, required=True)
    transcribe.add_argument(
        "--host", default=environment.get("WYOMING_STT_HOST", "whisper.agent.lan")
    )
    transcribe.add_argument(
        "--port", type=int, default=int(environment.get("WYOMING_STT_PORT", "10300"))
    )

    args = parser.parse_args(argv)
    proxy_url = None if args.direct else args.proxy
    factory = _client_factory(args.host, args.port, 60, proxy_url)
    if args.command == "synthesize":
        synthesize_to_path(
            args.text, args.output, WyomingTextToSpeechAdapter(factory)
        )
        print(args.output, file=output_stream)
        return 0

    transcript = transcribe_path(
        args.input, WyomingSpeechToTextAdapter(factory)
    )
    print(transcript, file=output_stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
