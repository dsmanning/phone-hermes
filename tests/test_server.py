import socket
import struct
import threading
import unittest
from uuid import UUID

from hermes_pbx_bridge.audiosocket import Frame, FrameDecoder, encode_frame
from hermes_pbx_bridge.server import AudioSocketServer
from hermes_pbx_bridge.turns import TurnDetector


class FakeProcessor:
    def __init__(self):
        self.calls = []

    def process_turn(self, session, pcm):
        self.calls.append((session.call_id, pcm))
        return struct.pack("<hh", 11, 22)


class AudioSocketServerTests(unittest.TestCase):
    def test_accepts_authorized_call_and_returns_audio_frame(self):
        processor = FakeProcessor()
        server = AudioSocketServer(
            ("127.0.0.1", 0),
            allowed_sources={"127.0.0.1"},
            processor=processor,
            detector_factory=lambda: TurnDetector(
                threshold=200,
                minimum_speech_ms=20,
                trailing_silence_ms=20,
                frame_ms=20,
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        call_id = UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4")
        speech = struct.pack("<" + "h" * 160, *([1000] * 160))
        silence = bytes(320)

        try:
            with socket.create_connection(server.server_address, timeout=2) as client:
                client.sendall(encode_frame(Frame(0x01, call_id.bytes)))
                client.sendall(encode_frame(Frame(0x10, speech)))
                client.sendall(encode_frame(Frame(0x10, silence)))
                response = client.recv(64)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(FrameDecoder().feed(response), [Frame(0x10, struct.pack("<hh", 11, 22))])
        self.assertEqual(processor.calls, [(call_id, speech + silence)])
        self.assertEqual(server.address_family, socket.AF_INET)


if __name__ == "__main__":
    unittest.main()
