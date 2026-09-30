import unittest

from hermes_pbx_bridge.config import Config


class ConfigTests(unittest.TestCase):
    def test_loads_ipv4_listener_and_restricted_source_from_environment(self):
        config = Config.from_environment(
            {
                "PBX_BRIDGE_BIND": "0.0.0.0",
                "PBX_BRIDGE_PORT": "8443",
                "PBX_BRIDGE_ALLOWED_SOURCES": "10.1.30.20",
                "HERMES_API_BASE_URL": "http://127.0.0.1:8642",
                "HERMES_API_KEY": "top-secret",
                "WYOMING_STT_HOST": "calliope.local",
                "WYOMING_STT_PORT": "10300",
                "WYOMING_TTS_HOST": "calliope.local",
                "WYOMING_TTS_PORT": "10200",
            }
        )

        self.assertEqual(config.bind_address, ("0.0.0.0", 8443))
        self.assertEqual(config.allowed_sources, frozenset({"10.1.30.20"}))
        self.assertEqual(config.responses_url, "http://127.0.0.1:8642/v1/responses")
        self.assertEqual(config.transcribe_url, "http://127.0.0.1:8642/api/audio/transcribe")
        self.assertEqual(config.speak_url, "http://127.0.0.1:8642/api/audio/speak")
        self.assertEqual(config.api_key, "top-secret")
        self.assertEqual(config.wyoming_stt_address, ("calliope.local", 10300))
        self.assertEqual(config.wyoming_tts_address, ("calliope.local", 10200))


if __name__ == "__main__":
    unittest.main()
