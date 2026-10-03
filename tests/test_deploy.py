from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_installer_does_not_require_pip_or_virtualenv(self):
        installer = (ROOT / "deploy" / "install.sh").read_text()
        unit = (ROOT / "deploy" / "hermes-pbx-bridge.service").read_text()

        self.assertNotIn("/venv", installer)
        self.assertNotIn(" pip ", installer)
        self.assertIn("WorkingDirectory=/opt/hermes-pbx-bridge/app", unit)
        self.assertIn("ExecStart=/usr/bin/python3 -m hermes_pbx_bridge.app", unit)

    def test_installer_waits_for_listener_startup(self):
        installer = (ROOT / "deploy" / "install.sh").read_text()

        self.assertIn("for attempt in {1..20}", installer)
        self.assertIn("/dev/tcp/127.0.0.1/8443", installer)

    def test_installer_configures_discovered_wyoming_services(self):
        installer = (ROOT / "deploy" / "install.sh").read_text()

        self.assertIn("WYOMING_STT_HOST=calliope.local", installer)
        self.assertIn("WYOMING_STT_PORT=10300", installer)
        self.assertIn("WYOMING_TTS_HOST=calliope.local", installer)
        self.assertIn("WYOMING_TTS_PORT=10200", installer)
        self.assertIn("HOME_ASSISTANT_API_BASE_URL=http://172.30.55.1", installer)
        self.assertIn("HOME_ASSISTANT_HOST=homeassistant.agent.lan", installer)
        self.assertIn("systemctl restart hermes-pbx-bridge.service", installer)

    def test_diagnostics_default_to_the_mounted_repository(self):
        diagnostics = (ROOT / "deploy" / "diagnose.sh").read_text()

        self.assertIn('SOURCE_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)', diagnostics)
        self.assertIn('OUTPUT=${OUTPUT:-"$SOURCE_ROOT/diagnostics/latest.txt"}', diagnostics)
        self.assertNotIn("/media/athena/chiron/hermes-output", diagnostics)


if __name__ == "__main__":
    unittest.main()
