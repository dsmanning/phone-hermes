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

    def process_turn(self, session, pcm, progress=None):
        self.calls.append((session.call_id, pcm))
        return struct.pack("<hh", 11, 22)

    def begin_call(self, session):
        return b""

    def end_of_command(self):
        return b""


class GreetingProcessor(FakeProcessor):
    def begin_call(self, session):
        return struct.pack("<hh", 5, 6)


class PacingProcessor(FakeProcessor):
    def begin_call(self, session):
        return bytes(640)


class ToneProcessor(FakeProcessor):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def end_of_command(self):
        return struct.pack("<hh", 7, 8)

    def process_turn(self, session, pcm, progress=None):
        self.started.set()
        self.release.wait(timeout=2)
        return b""


class BlockingProcessor(FakeProcessor):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def process_turn(self, session, pcm, progress=None):
        self.started.set()
        self.release.wait(timeout=2)
        return b""


class AudioSocketServerTests(unittest.TestCase):
    def test_paces_outbound_audio_at_twenty_milliseconds_per_frame(self):
        delays = []
        server = AudioSocketServer(
            ("127.0.0.1", 0),
            allowed_sources={"127.0.0.1"},
            processor=PacingProcessor(),
            detector_factory=lambda: TurnDetector(
                threshold=200,
                minimum_speech_ms=20,
                trailing_silence_ms=20,
                frame_ms=20,
            ),
            sleeper=delays.append,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        call_id = UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4")
        decoder = FrameDecoder()
        frames = []

        try:
            with socket.create_connection(server.server_address, timeout=2) as client:
                client.sendall(encode_frame(Frame(0x01, call_id.bytes)))
                while len(frames) < 2:
                    frames.extend(decoder.feed(client.recv(1024)))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(frames, [Frame(0x10, bytes(320)), Frame(0x10, bytes(320))])
        self.assertEqual(delays, [0.02])

    def test_sends_beep_when_turn_ends_before_processing(self):
        processor = ToneProcessor()
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

        try:
            with socket.create_connection(server.server_address, timeout=2) as client:
                client.sendall(encode_frame(Frame(0x01, call_id.bytes)))
                client.sendall(encode_frame(Frame(0x10, speech)))
                client.sendall(encode_frame(Frame(0x10, bytes(320))))
                response = client.recv(64)
                self.assertTrue(processor.started.wait(timeout=1))
        finally:
            processor.release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(
            FrameDecoder().feed(response),
            [Frame(0x10, struct.pack("<hh", 7, 8))],
        )

    def test_keeps_reading_call_frames_while_command_is_processing(self):
        processor = BlockingProcessor()
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

        try:
            with socket.create_connection(server.server_address, timeout=2) as client:
                client.sendall(encode_frame(Frame(0x01, call_id.bytes)))
                client.sendall(encode_frame(Frame(0x10, speech)))
                client.sendall(encode_frame(Frame(0x10, bytes(320))))
                self.assertTrue(processor.started.wait(timeout=1))
                client.sendall(encode_frame(Frame(0x00, b"")))
                client.settimeout(0.5)
                self.assertEqual(client.recv(1), b"")
        finally:
            processor.release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_sends_opening_prompt_immediately_after_call_uuid(self):
        server = AudioSocketServer(
            ("127.0.0.1", 0),
            allowed_sources={"127.0.0.1"},
            processor=GreetingProcessor(),
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

        try:
            with socket.create_connection(server.server_address, timeout=2) as client:
                client.sendall(encode_frame(Frame(0x01, call_id.bytes)))
                response = client.recv(64)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(
            FrameDecoder().feed(response),
            [Frame(0x10, struct.pack("<hh", 5, 6))],
        )

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

    def test_logs_call_boundaries_without_audio_or_transcript_content(self):
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

        try:
            with self.assertLogs("hermes_pbx_bridge.server", level="INFO") as captured:
                with socket.create_connection(server.server_address, timeout=2) as client:
                    client.sendall(encode_frame(Frame(0x01, call_id.bytes)))
                    client.sendall(encode_frame(Frame(0x10, speech)))
                    client.sendall(encode_frame(Frame(0x10, bytes(320))))
                    client.recv(64)
                    client.sendall(encode_frame(Frame(0x00, b"")))
                    client.shutdown(socket.SHUT_WR)
                    self.assertEqual(client.recv(1), b"")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        messages = "\n".join(captured.output)
        self.assertIn("AudioSocket call accepted", messages)
        self.assertIn("AudioSocket turn detected pcm_bytes=640", messages)
        self.assertIn("AudioSocket reply sent pcm_bytes=4", messages)
        self.assertIn("AudioSocket call closed", messages)
        self.assertNotIn(str(call_id), messages)


if __name__ == "__main__":
    unittest.main()
