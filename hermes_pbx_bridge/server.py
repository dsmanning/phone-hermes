from collections.abc import Callable, Iterable
import socket
import socketserver
from typing import Protocol
from uuid import UUID

from .audiosocket import Frame, encode_frame
from .security import ConnectionGuard
from .sessions import CallSession, SessionRegistry
from .turns import TurnDetector


class Processor(Protocol):
    def process_turn(self, session: CallSession, pcm: bytes) -> bytes: ...


class _AudioSocketHandler(socketserver.BaseRequestHandler):
    server: "AudioSocketServer"

    def handle(self) -> None:
        call_id: UUID | None = None
        try:
            self.server.guard.authorize_source(self.client_address[0])
            first = self._read_frame()
            if first.kind != 0x01:
                raise ValueError("first AudioSocket frame must contain the call UUID")
            call_id = self.server.guard.parse_call_uuid(first.payload)
            session = self.server.sessions.open(call_id)
            detector = self.server.detector_factory()

            while True:
                frame = self._read_frame()
                if frame.kind == 0x00:
                    return
                if frame.kind != 0x10:
                    continue
                turn = detector.feed(frame.payload)
                if turn is None:
                    continue
                reply = self.server.processor.process_turn(session, turn)
                for offset in range(0, len(reply), 320):
                    self.request.sendall(encode_frame(Frame(0x10, reply[offset : offset + 320])))
        except (ConnectionError, EOFError, PermissionError, ValueError):
            return
        finally:
            if call_id is not None:
                self.server.sessions.close(call_id)

    def _read_frame(self) -> Frame:
        header = self._read_exact(3)
        length = int.from_bytes(header[1:3], "big")
        return Frame(header[0], self._read_exact(length))

    def _read_exact(self, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            chunk = self.request.recv(length - len(chunks))
            if not chunk:
                raise EOFError("AudioSocket connection closed")
            chunks.extend(chunk)
        return bytes(chunks)


class AudioSocketServer(socketserver.ThreadingTCPServer):
    address_family = socket.AF_INET
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        allowed_sources: Iterable[str],
        processor: Processor,
        detector_factory: Callable[[], TurnDetector],
    ) -> None:
        self.guard = ConnectionGuard(allowed_sources)
        self.processor = processor
        self.detector_factory = detector_factory
        self.sessions = SessionRegistry()
        super().__init__(server_address, _AudioSocketHandler)
