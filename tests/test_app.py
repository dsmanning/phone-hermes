import socket
import unittest

from hermes_pbx_bridge.app import build_server
from hermes_pbx_bridge.config import Config
from hermes_pbx_bridge.wyoming import WyomingSpeechToTextAdapter, WyomingTextToSpeechAdapter


class UnusedTransport:
    def request(self, request, timeout):
        raise AssertionError("not called while building the server")


class ApplicationTests(unittest.TestCase):
    def test_builds_ipv4_server_from_runtime_configuration(self):
        config = Config(
            bind_host="127.0.0.1",
            port=0,
            allowed_sources=frozenset({"127.0.0.1"}),
            api_base_url="http://127.0.0.1:8642",
            api_key="secret",
        )

        server = build_server(config, transport=UnusedTransport())
        try:
            self.assertEqual(server.address_family, socket.AF_INET)
            self.assertEqual(server.server_address[0], "127.0.0.1")
            self.assertGreater(server.server_address[1], 0)
            self.assertIsInstance(server.processor._stt, WyomingSpeechToTextAdapter)
            self.assertIsInstance(server.processor._tts, WyomingTextToSpeechAdapter)
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
