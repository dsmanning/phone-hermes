import io
import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request

from hermes_pbx_bridge.adapters import (
    AdapterError,
    HermesResponsesAdapter,
    HermesSpeechToTextAdapter,
    HomeAssistantConversationAdapter,
    HttpResponse,
    SpeechToTextAdapter,
    TextToSpeechAdapter,
    UrllibTransport,
)


class RecordingTransport:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def request(self, request, timeout):
        self.requests.append((request, timeout))
        return self.response


class TimeoutTransport:
    def request(self, request, timeout):
        raise TimeoutError("upstream timed out with token secret")


class ConnectionErrorTransport:
    def request(self, request, timeout):
        raise OSError("network details must not leak")


class FakeUrlResponse:
    status = 201
    headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"ok":true}'


class UrllibTransportTests(unittest.TestCase):
    def test_returns_status_headers_and_body_from_opener(self):
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            return FakeUrlResponse()

        transport = UrllibTransport(opener=opener)

        response = transport.request(Request("http://127.0.0.1/test"), 4)

        self.assertEqual(calls, [("http://127.0.0.1/test", 4)])
        self.assertEqual(response, HttpResponse(201, {"Content-Type": "application/json"}, b'{"ok":true}'))

    def test_converts_http_error_to_response_for_adapter_handling(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url,
                503,
                "Unavailable",
                {"Content-Type": "text/plain"},
                io.BytesIO(b"do not leak this body"),
            )

        response = UrllibTransport(opener=opener).request(
            Request("http://127.0.0.1/test"), 4
        )

        self.assertEqual(response.status, 503)
        self.assertEqual(response.body, b"do not leak this body")


class HermesResponsesAdapterTests(unittest.TestCase):
    def test_sends_previous_response_id_and_returns_new_conversation_state(self):
        response_body = {
            "id": "resp_next",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "The door is locked."}],
                }
            ],
        }
        transport = RecordingTransport(
            HttpResponse(200, {"content-type": "application/json"}, json.dumps(response_body).encode())
        )
        adapter = HermesResponsesAdapter(
            "http://127.0.0.1:8642/v1/responses", "secret", transport, timeout=12
        )

        reply = adapter.respond("Lock the door", "resp_previous")

        request, timeout = transport.requests[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["previous_response_id"], "resp_previous")
        self.assertTrue(payload["store"])
        self.assertEqual(timeout, 12)
        self.assertEqual(reply.text, "The door is locked.")
        self.assertEqual(reply.response_id, "resp_next")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")

    def test_timeout_becomes_sanitized_adapter_error(self):
        adapter = HermesResponsesAdapter(
            "http://127.0.0.1:8642/v1/responses", "secret", TimeoutTransport(), timeout=3
        )

        with self.assertRaises(AdapterError) as raised:
            adapter.respond("hello", None)

        self.assertEqual(str(raised.exception), "Hermes request timed out")
        self.assertNotIn("secret", str(raised.exception))


class HomeAssistantConversationAdapterTests(unittest.TestCase):
    def test_connection_failure_becomes_sanitized_adapter_error(self):
        adapter = HomeAssistantConversationAdapter(
            "http://172.30.55.1/api/conversation/process",
            "homeassistant.agent.lan",
            ConnectionErrorTransport(),
            timeout=8,
        )

        with self.assertRaises(AdapterError) as raised:
            adapter.respond("turn on the kitchen lights")

        self.assertEqual(str(raised.exception), "Home Assistant request failed")
        self.assertNotIn("network details", str(raised.exception))

    def test_returns_spoken_result_for_handled_command(self):
        body = {
            "response": {
                "speech": {"plain": {"speech": "Turned on the kitchen lights."}},
                "response_type": "action_done",
                "data": {"success": [{"id": "light.kitchen"}], "failed": []},
            }
        }
        transport = RecordingTransport(
            HttpResponse(200, {"content-type": "application/json"}, json.dumps(body).encode())
        )
        adapter = HomeAssistantConversationAdapter(
            "http://172.30.55.1/api/conversation/process",
            "homeassistant.agent.lan",
            transport,
            timeout=8,
        )

        result = adapter.respond("turn on the kitchen lights")

        request, timeout = transport.requests[0]
        self.assertEqual(
            json.loads(request.data),
            {"text": "turn on the kitchen lights", "language": "en"},
        )
        self.assertEqual(request.headers["Host"], "homeassistant.agent.lan")
        self.assertEqual(timeout, 8)
        self.assertEqual(result, "Turned on the kitchen lights.")

    def test_returns_none_when_home_assistant_does_not_recognize_intent(self):
        body = {
            "response": {
                "speech": {"plain": {"speech": "Sorry, I couldn't understand that."}},
                "response_type": "error",
                "data": {"code": "no_intent_match"},
            }
        }
        adapter = HomeAssistantConversationAdapter(
            "http://172.30.55.1/api/conversation/process",
            "homeassistant.agent.lan",
            RecordingTransport(
                HttpResponse(200, {"content-type": "application/json"}, json.dumps(body).encode())
            ),
            timeout=8,
        )

        self.assertIsNone(adapter.respond("write an email"))


class HermesSpeechToTextAdapterTests(unittest.TestCase):
    def test_encodes_wave_for_hermes_audio_relay(self):
        transport = RecordingTransport(
            HttpResponse(
                200,
                {"content-type": "application/json"},
                b'{"ok":true,"transcript":"hello"}',
            )
        )
        adapter = HermesSpeechToTextAdapter(
            "http://127.0.0.1:8642/api/audio/transcribe", "secret", transport, timeout=30
        )

        text = adapter.transcribe(b"RIFF")

        request, timeout = transport.requests[0]
        self.assertEqual(
            json.loads(request.data),
            {"data_url": "data:audio/wav;base64,UklGRg==", "mime_type": "audio/wav"},
        )
        self.assertEqual(timeout, 30)
        self.assertEqual(text, "hello")


class SpeechToTextAdapterTests(unittest.TestCase):
    def test_returns_text_from_json_response(self):
        transport = RecordingTransport(
            HttpResponse(200, {"content-type": "application/json"}, b'{"text":"hello"}')
        )
        adapter = SpeechToTextAdapter(
            "http://calliope.home.arpa:8000/transcribe", "secret", transport, timeout=8
        )

        text = adapter.transcribe(b"RIFF fake wave")

        request, timeout = transport.requests[0]
        self.assertEqual(request.data, b"RIFF fake wave")
        self.assertEqual(request.headers["Content-type"], "audio/wav")
        self.assertEqual(timeout, 8)
        self.assertEqual(text, "hello")

    def test_timeout_becomes_sanitized_adapter_error(self):
        adapter = SpeechToTextAdapter(
            "http://calliope.home.arpa:8000/v1/audio/transcriptions",
            "secret",
            TimeoutTransport(),
            timeout=8,
        )

        with self.assertRaises(AdapterError) as raised:
            adapter.transcribe(b"RIFF fake wave")

        self.assertEqual(str(raised.exception), "speech-to-text request timed out")
        self.assertNotIn("secret", str(raised.exception))

    def test_http_failure_does_not_expose_response_or_credentials(self):
        transport = RecordingTransport(
            HttpResponse(503, {"content-type": "text/plain"}, b"backend leaked secret")
        )
        adapter = SpeechToTextAdapter(
            "http://calliope.home.arpa:8000/transcribe", "secret", transport, timeout=8
        )

        with self.assertRaises(AdapterError) as raised:
            adapter.transcribe(b"RIFF fake wave")

        self.assertEqual(str(raised.exception), "speech-to-text request failed with HTTP 503")
        self.assertNotIn("secret", str(raised.exception))


class TextToSpeechAdapterTests(unittest.TestCase):
    def test_decodes_audio_data_url_from_json_response(self):
        transport = RecordingTransport(
            HttpResponse(
                200,
                {"content-type": "application/json"},
                b'{"ok":true,"data_url":"data:audio/wav;base64,UklGRg=="}',
            )
        )
        adapter = TextToSpeechAdapter(
            "http://127.0.0.1:8642/api/audio/speak", "secret", transport, timeout=10
        )

        audio = adapter.synthesize("hello")

        request, timeout = transport.requests[0]
        self.assertEqual(json.loads(request.data), {"text": "hello"})
        self.assertEqual(timeout, 10)
        self.assertEqual(audio, b"RIFF")

    def test_timeout_becomes_sanitized_adapter_error(self):
        adapter = TextToSpeechAdapter(
            "http://calliope.home.arpa:8001/synthesize",
            "secret",
            TimeoutTransport(),
            timeout=10,
        )

        with self.assertRaises(AdapterError) as raised:
            adapter.synthesize("hello")

        self.assertEqual(str(raised.exception), "text-to-speech request timed out")
        self.assertNotIn("secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
