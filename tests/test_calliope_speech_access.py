from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "provision-calliope-speech-access.sh"
ROLLBACK = ROOT / "rollback-calliope-speech-access.sh"

COMPOSE = """services:
  egress-proxy:
    image: squid
    networks:
      - dev-internal
      - egress
      - hermes-agent-lan
  dev:
    image: hermes-dev
    networks:
      dev-internal:
        ipv4_address: 172.21.0.10
    volumes:
      - ${PHONE_HERMES_REPO}:/workspace/phone-hermes
networks:
  dev-internal:
    internal: true
  egress:
    driver: bridge
  hermes-agent-lan:
    external: true
    name: hermes-agent-lan
"""

SQUID = """http_port 3128
acl CONNECT method CONNECT
acl SSL_ports port 443
acl dev_backend src 172.21.0.10/32
acl private_networks dst 172.16.0.0/12
http_access deny private_networks
http_access deny !CONNECT
http_access deny !SSL_ports
http_access deny all
"""

DNSMASQ = """no-resolv
bind-interfaces
listen-address=172.30.55.1
address=/frigate.agent.lan/172.30.55.1
"""

FIREWALL = """#!/bin/sh
apply_rules() {
    "$IPT" -A "$CHAIN" -p tcp -d "$GATEWAY" --dport 80 -j ACCEPT
    "$IPT" -A "$CHAIN" -j DROP
}
"""


class CalliopeSpeechAccessProvisioningTests(unittest.TestCase):
    def test_runtime_recreation_reuses_local_images(self):
        script = SCRIPT.read_text()
        recreate = next(
            line
            for line in script.splitlines()
            if "up -d" in line and "egress-proxy dev" in line
        )

        self.assertIn("--no-build", recreate)
        self.assertNotIn("--build", recreate)

    def make_fixture(self, directory):
        root = Path(directory)
        backend = root / "backend"
        gateway = root / "gateway"
        units = root / "units"
        output = root / "output"
        for path in (backend, gateway, units, output):
            path.mkdir()
        (backend / "compose.yaml").write_text(COMPOSE)
        (backend / "squid.conf").write_text(SQUID)
        (gateway / "dnsmasq.conf").write_text(DNSMASQ)
        firewall = root / "firewall"
        firewall.write_text(FIREWALL)
        return root, backend, gateway, units, output, firewall

    def environment(self, backend, gateway, units, output, firewall):
        return {
            **os.environ,
            "BACKEND_ROOT": str(backend),
            "GATEWAY_ROOT": str(gateway),
            "UNIT_ROOT": str(units),
            "OUTPUT_SOURCE": str(output),
            "FIREWALL_FILE": str(firewall),
        }

    def test_prepare_scopes_dns_tcp_proxy_firewall_and_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root, backend, gateway, units, output, firewall = self.make_fixture(directory)
            result = subprocess.run(
                [str(SCRIPT), "--prepare-only"],
                env=self.environment(backend, gateway, units, output, firewall),
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            dns = (gateway / "dnsmasq.conf").read_text()
            self.assertIn("address=/piper.agent.lan/172.30.55.1", dns)
            self.assertIn("address=/whisper.agent.lan/172.30.55.1", dns)

            rules = firewall.read_text()
            self.assertIn('--dport 10200 -j ACCEPT', rules)
            self.assertIn('--dport 10300 -j ACCEPT', rules)
            self.assertLess(rules.index('--dport 10200'), rules.index('-j DROP'))

            piper_socket = (units / "hermes-calliope-piper-proxy.socket").read_text()
            piper_service = (units / "hermes-calliope-piper-proxy.service").read_text()
            whisper_socket = (units / "hermes-calliope-whisper-proxy.socket").read_text()
            whisper_service = (units / "hermes-calliope-whisper-proxy.service").read_text()
            self.assertIn("ListenStream=172.30.55.1:10200", piper_socket)
            self.assertIn("calliope.home.arpa:10200", piper_service)
            self.assertIn("ListenStream=172.30.55.1:10300", whisper_socket)
            self.assertIn("calliope.home.arpa:10300", whisper_service)

            squid = (backend / "squid.conf").read_text()
            self.assertIn("acl calliope_speech dstdomain piper.agent.lan whisper.agent.lan", squid)
            self.assertIn("acl calliope_speech_ports port 10200 10300", squid)
            allow = "http_access allow dev_backend CONNECT calliope_speech calliope_speech_ports"
            self.assertLess(squid.index(allow), squid.index("http_access deny private_networks"))
            self.assertNotIn("192.168.1.63", squid)

            compose = (backend / "compose.yaml").read_text()
            self.assertIn('"piper.agent.lan:172.30.55.1"', compose)
            self.assertIn('"whisper.agent.lan:172.30.55.1"', compose)
            self.assertIn(f"{output}:/nas-output", compose)
            dev_section = compose.split("  dev:", 1)[1].split("networks:", 1)[0]
            self.assertNotIn("hermes-agent-lan", dev_section)

    def test_rollback_restores_all_mutated_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root, backend, gateway, units, output, firewall = self.make_fixture(directory)
            env = self.environment(backend, gateway, units, output, firewall)
            prepared = subprocess.run(
                [str(SCRIPT), "--prepare-only"], env=env, text=True, capture_output=True
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)

            rolled_back = subprocess.run(
                [str(ROLLBACK), "--prepare-only"], env=env, text=True, capture_output=True
            )

            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
            self.assertEqual((backend / "compose.yaml").read_text(), COMPOSE)
            self.assertEqual((backend / "squid.conf").read_text(), SQUID)
            self.assertEqual((gateway / "dnsmasq.conf").read_text(), DNSMASQ)
            self.assertEqual(firewall.read_text(), FIREWALL)
            self.assertFalse((units / "hermes-calliope-piper-proxy.socket").exists())
            self.assertFalse((units / "hermes-calliope-whisper-proxy.socket").exists())


if __name__ == "__main__":
    unittest.main()
