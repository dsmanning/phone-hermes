from collections.abc import Callable, Iterable
import logging
import socket
import socketserver
import threading
import time
from typing import Protocol
from uuid import UUID

from .audiosocket import Frame, encode_frame
from .security import ConnectionGuard
from .sessions import CallSession, SessionRegistry
from .turns import TurnDetector


logger = logging.getLogger(__name__)


class Processor(Protocol):
    def begin_call(self, session: CallSession) -> bytes: ...

    def end_of_command(self) -> bytes: ...

    def process_turn(
        self,
        session: CallSession,
        pcm: bytes,
        progress: Callable[[bytes], None] | None = None,
    ) -> bytes: ...


class _AudioSocketHandler(socketserver.BaseRequestHandler):
    server: "AudioSocketServer"

    def handle(self) -> None:
        call_id: UUID | None = None
        self._send_lock = threading.Lock()
        processing = threading.Event()
        try:
            self.server.guard.authorize_source(self.client_address[0])
            first = self._read_frame()
            if first.kind != 0x01:
                raise ValueError("first AudioSocket frame must contain the call UUID")
            call_id = self.server.guard.parse_call_uuid(first.payload)
            session = self.server.sessions.open(call_id)
            detector = self.server.detector_factory()
            logger.info("AudioSocket call accepted")
            opening = self.server.processor.begin_call(session)
            self._send_audio(opening)
            logger.info("AudioSocket opening prompt sent pcm_bytes=%d", len(opening))

            while True:
                frame = self._read_frame()
                if frame.kind == 0x00:
                    return
                if frame.kind != 0x10:
                    continue
                if processing.is_set():
                    continue
                turn = detector.feed(frame.payload)
                if turn is None:
                    continue
                logger.info("AudioSocket turn detected pcm_bytes=%d", len(turn))
                tone = self.server.processor.end_of_command()
                self._send_audio(tone)
                logger.info("AudioSocket command-end tone sent pcm_bytes=%d", len(tone))
                processing.set()
                threading.Thread(
                    target=self._process_turn,
                    args=(session, turn, processing),
                    daemon=True,
                ).start()
        except (ConnectionError, EOFError, PermissionError, ValueError):
            return
        finally:
            if call_id is not None:
                self.server.sessions.close(call_id)
                logger.info("AudioSocket call closed")

    def _read_frame(self) -> Frame:
        header = self._read_exact(3)
        length = int.from_bytes(header[1:3], "big")
        return Frame(header[0], self._read_exact(length))

    def _send_audio(self, pcm: bytes) -> None:
        with self._send_lock:
            for offset in range(0, len(pcm), 320):
                if offset:
                    self.server.sleeper(0.02)
                self.request.sendall(encode_frame(Frame(0x10, pcm[offset : offset + 320])))

    def _process_turn(
        self,
        session: CallSession,
        turn: bytes,
        processing: threading.Event,
    ) -> None:
        try:
            reply = self.server.processor.process_turn(session, turn, self._send_progress)
            self._send_audio(reply)
            logger.info("AudioSocket reply sent pcm_bytes=%d", len(reply))
        except Exception as exc:
            logger.error("AudioSocket turn failed error=%s", type(exc).__name__)
        finally:
            processing.clear()

    def _send_progress(self, pcm: bytes) -> None:
        self._send_audio(pcm)
        logger.info("AudioSocket progress sent pcm_bytes=%d", len(pcm))

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
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.guard = ConnectionGuard(allowed_sources)
        self.processor = processor
        self.detector_factory = detector_factory
        self.sleeper = sleeper
        self.sessions = SessionRegistry()
        super().__init__(server_address, _AudioSocketHandler)
