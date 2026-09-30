from dataclasses import dataclass, field
import io
import json
import socket
from typing import Any, BinaryIO, Callable, Protocol
import wave


@dataclass(frozen=True, slots=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    payload: bytes | None = None


def encode_event(event: Event) -> bytes:
    header: dict[str, Any] = {"type": event.type, "version": "1.8.0"}
    if event.data:
        # Wyoming readers accept inline event metadata. Keeping request metadata
        # in the header also avoids an unnecessary second framing segment.
        header["data"] = event.data
    payload = event.payload or b""
    if payload:
        header["payload_length"] = len(payload)
    return json.dumps(header, separators=(",", ":")).encode() + b"\n" + payload


def read_event(reader: BinaryIO) -> Event:
    line = reader.readline()
    if not line:
        raise EOFError("Wyoming connection closed")
    header = json.loads(line)
    data_length = int(header.get("data_length", 0))
    data = json.loads(_read_exact(reader, data_length)) if data_length else header.get("data", {})
    payload_length = int(header.get("payload_length", 0))
    payload = _read_exact(reader, payload_length) if payload_length else None
    return Event(header["type"], data, payload)


class SocketEventClient:
    def __init__(
        self,
        host: str,
        port: int,
        timeout: float,
        *,
        connector: Callable = socket.create_connection,
    ) -> None:
        self._socket = connector((host, port), timeout)
        self._reader = self._socket.makefile("rb")

    def __enter__(self) -> "SocketEventClient":
        return self

    def __exit__(self, *args: object) -> bool:
        self._reader.close()
        self._socket.close()
        return False

    def send(self, event: Event) -> None:
        self._socket.sendall(encode_event(event))

    def receive(self) -> Event:
        return read_event(self._reader)


class EventClient(Protocol):
    def __enter__(self) -> "EventClient": ...
    def __exit__(self, *args: object) -> bool: ...
    def send(self, event: Event) -> None: ...
    def receive(self) -> Event: ...


class WyomingSpeechToTextAdapter:
    def __init__(self, client_factory: Callable[[], EventClient]) -> None:
        self._client_factory = client_factory

    def transcribe(self, wav_audio: bytes) -> str:
        with wave.open(io.BytesIO(wav_audio), "rb") as wav_file:
            rate = wav_file.getframerate()
            width = wav_file.getsampwidth()
            channels = wav_file.getnchannels()
            pcm = wav_file.readframes(wav_file.getnframes())
        audio_format = {"rate": rate, "width": width, "channels": channels}
        with self._client_factory() as client:
            client.send(Event("transcribe"))
            client.send(Event("audio-start", audio_format))
            client.send(Event("audio-chunk", audio_format, pcm))
            client.send(Event("audio-stop"))
            while True:
                response = client.receive()
                if response.type == "transcript":
                    return str(response.data["text"]).strip()
                if response.type == "error":
                    raise RuntimeError("Wyoming speech-to-text request failed")


class WyomingTextToSpeechAdapter:
    def __init__(self, client_factory: Callable[[], EventClient]) -> None:
        self._client_factory = client_factory

    def synthesize(self, text: str) -> bytes:
        pcm = bytearray()
        audio_format: dict[str, Any] | None = None
        with self._client_factory() as client:
            client.send(Event("synthesize", {"text": text}))
            while True:
                response = client.receive()
                if response.type == "audio-start":
                    audio_format = response.data
                elif response.type == "audio-chunk":
                    audio_format = audio_format or response.data
                    pcm.extend(response.payload or b"")
                elif response.type == "audio-stop":
                    break
                elif response.type == "error":
                    raise RuntimeError("Wyoming text-to-speech request failed")
        if audio_format is None:
            raise RuntimeError("Wyoming text-to-speech returned no audio format")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setframerate(int(audio_format["rate"]))
            wav_file.setsampwidth(int(audio_format["width"]))
            wav_file.setnchannels(int(audio_format["channels"]))
            wav_file.writeframes(bytes(pcm))
        return output.getvalue()


def _read_exact(reader: BinaryIO, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = reader.read(length - len(chunks))
        if not chunk:
            raise EOFError("Wyoming connection closed mid-event")
        chunks.extend(chunk)
    return bytes(chunks)
