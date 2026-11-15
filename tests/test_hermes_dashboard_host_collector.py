from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "collect-hermes-dashboard-host.sh"


class HermesDashboardHostCollectorTests(unittest.TestCase):
    def test_collector_captures_required_boundaries_without_secrets(self):
        text = SCRIPT.read_text()

        self.assertIn("chiron.local", text)
        self.assertNotIn("hermes.home.arpa", text)
        self.assertIn("sport = :443 or sport = :9119", text)
        self.assertIn("caddy list-modules", text)
        self.assertIn("docker ps", text)
        self.assertIn("docker inspect hermes-lan-gateway", text)
        self.assertIn("/opt/hermes-lan-gateway/Caddyfile", text)
        self.assertIn("/etc/caddy/Caddyfile", text)
        self.assertIn("/usr/local/sbin/hermes-agent-lan-firewall", text)
        self.assertIn("ip -4 address show", text)
        self.assertIn("ip -4 route show", text)
        self.assertIn("iptables-save", text)
        self.assertIn("ufw status verbose", text)
        self.assertIn("systemctl", text)
        self.assertIn("hermes dashboard --help", text)
        self.assertIn("readlink -f", text)
        self.assertIn("node --version", text)
        self.assertIn("systemctl status hermes-dashboard.service", text)
        self.assertIn("systemctl status hermes-dashboard-firewall.service", text)
        self.assertIn("journalctl --no-pager -n 160 -u hermes-dashboard.service", text)
        self.assertIn("journalctl --no-pager -n 160 -u caddy.service", text)
        self.assertIn("curl -4 -sS", text)
        self.assertIn("getent ahostsv4 chiron.local", text)
        self.assertIn("COMPLETED hermes-dashboard-host discovery", text)
        self.assertNotIn(".hermes/.env", text)
        self.assertNotIn("printenv", text)

    def test_collector_writes_to_an_overridable_diagnostics_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "host-report.txt"
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            sudo = fake_bin / "sudo"
            sudo.write_text("#!/bin/sh\nshift 2>/dev/null || true\nexec \"$@\"\n")
            sudo.chmod(0o755)

            result = subprocess.run(
                [str(SCRIPT)],
                env={
                    **os.environ,
                    "OUTPUT": str(output),
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "SKIP_SUDO_PREFLIGHT": "1",
                },
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.exists())
            report = output.read_text()
            self.assertIn("Target hostname: chiron.local", report)
            self.assertIn("COMPLETED hermes-dashboard-host discovery", report)
            self.assertIn(str(output), result.stdout)


if __name__ == "__main__":
    unittest.main()
