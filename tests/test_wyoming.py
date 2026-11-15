import io
import json
import struct
import unittest
import wave

from hermes_pbx_bridge.wyoming import (
    Event,
    SocketEventClient,
    WyomingSpeechToTextAdapter,
    WyomingTextToSpeechAdapter,
    create_http_connect_connection,
    encode_event,
    read_event,
)


class WyomingProtocolTests(unittest.TestCase):
    def test_http_connect_connector_opens_tunnel_without_resolving_target(self):
        class FakeSocket:
            def __init__(self):
                self.sent = bytearray()
                self.response = io.BytesIO(
                    b"HTTP/1.1 200 Connection established\r\nProxy-Agent: test\r\n\r\n"
                )

            def sendall(self, data):
                self.sent.extend(data)

            def recv(self, length):
                return self.response.read(length)

            def close(self):
                pass

        sock = FakeSocket()
        calls = []

        def connector(address, timeout):
            calls.append((address, timeout))
            return sock

        actual = create_http_connect_connection(
            ("piper.agent.lan", 10200),
            8,
            proxy_address=("egress-proxy", 3128),
            connector=connector,
        )

        self.assertIs(actual, sock)
        self.assertEqual(calls, [(('egress-proxy', 3128), 8)])
        self.assertEqual(
            bytes(sock.sent),
            b"CONNECT piper.agent.lan:10200 HTTP/1.1\r\n"
            b"Host: piper.agent.lan:10200\r\n\r\n",
        )

    def test_round_trips_json_data_and_binary_payload(self):
        original = Event(
            "audio-chunk",
            {"rate": 8000, "width": 2, "channels": 1},
            b"\x01\x02\x03\x04",
        )

        encoded = encode_event(original)
        decoded = read_event(io.BytesIO(encoded))

        self.assertEqual(decoded, original)

    def test_encode_event_embeds_data_in_json_header(self):
        event = Event(
            "audio-chunk",
            {"rate": 8000, "width": 2, "channels": 1},
            b"\x01\x02",
        )

        encoded = encode_event(event)
        header_bytes, payload = encoded.split(b"\n", 1)
        header = json.loads(header_bytes)

        self.assertEqual(
            header["data"], {"rate": 8000, "width": 2, "channels": 1}
        )
        self.assertNotIn("data_length", header)
        self.assertEqual(payload, b"\x01\x02")

    def test_socket_client_sends_and_receives_events(self):
        response = Event("transcript", {"text": "hello"})

        class FakeSocket:
            def __init__(self):
                self.sent = bytearray()
                self.reader = io.BytesIO(encode_event(response))
                self.closed = False

            def sendall(self, data):
                self.sent.extend(data)

            def makefile(self, mode):
                return self.reader

            def close(self):
                self.closed = True

        sock = FakeSocket()
        calls = []

        def connector(address, timeout):
            calls.append((address, timeout))
            return sock

        with SocketEventClient("calliope.local", 10300, 8, connector=connector) as client:
            client.send(Event("describe"))
            actual = client.receive()

        self.assertEqual(calls, [(('calliope.local', 10300), 8)])
        self.assertEqual(bytes(sock.sent), encode_event(Event("describe")))
        self.assertEqual(actual, response)
        self.assertTrue(sock.closed)


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def send(self, event):
        self.sent.append(event)

    def receive(self):
        return next(self.responses)


class WyomingSpeechToTextAdapterTests(unittest.TestCase):
    def test_streams_wave_audio_and_returns_transcript(self):
        client = FakeClient([Event("transcript", {"text": "testing connection"})])
        adapter = WyomingSpeechToTextAdapter(lambda: client)
        pcm = struct.pack("<hh", 100, 200)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(8000)
            wav_file.writeframes(pcm)

        text = adapter.transcribe(wav_buffer.getvalue())

        self.assertEqual(text, "testing connection")
        self.assertEqual(client.sent[0], Event("transcribe"))
        self.assertEqual(
            client.sent[1], Event("audio-start", {"rate": 8000, "width": 2, "channels": 1})
        )
        self.assertEqual(
            client.sent[2],
            Event("audio-chunk", {"rate": 8000, "width": 2, "channels": 1}, pcm),
        )
        self.assertEqual(client.sent[3], Event("audio-stop"))


class WyomingTextToSpeechAdapterTests(unittest.TestCase):
    def test_collects_pcm_audio_into_wave_response(self):
        audio_format = {"rate": 16000, "width": 2, "channels": 1}
        pcm = struct.pack("<hh", 300, 400)
        client = FakeClient(
            [
                Event("audio-start", audio_format),
                Event("audio-chunk", audio_format, pcm),
                Event("audio-stop"),
            ]
        )
        adapter = WyomingTextToSpeechAdapter(lambda: client)

        wav_audio = adapter.synthesize("Connection working")

        self.assertEqual(client.sent, [Event("synthesize", {"text": "Connection working"})])
        with wave.open(io.BytesIO(wav_audio), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.readframes(wav_file.getnframes()), pcm)


if __name__ == "__main__":
    unittest.main()
