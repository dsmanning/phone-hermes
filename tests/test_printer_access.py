from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "provision-dell-printer-access.sh"
ROLLBACK = ROOT / "rollback-dell-printer-access.sh"

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

FIREWALL = """#!/bin/sh
apply_rules() {
    "$IPT" -A "$CHAIN" -p tcp -d "$GATEWAY" --dport 80 -j ACCEPT
    "$IPT" -A "$CHAIN" -j DROP
}
"""


class DellPrinterAccessProvisioningTests(unittest.TestCase):
    def make_fixture(self, directory):
        root = Path(directory)
        backend = root / "backend"
        units = root / "units"
        backend.mkdir()
        units.mkdir()
        (backend / "compose.yaml").write_text(COMPOSE)
        (backend / "squid.conf").write_text(SQUID)
        firewall = root / "firewall"
        firewall.write_text(FIREWALL)
        return backend, units, firewall

    def environment(self, backend, units, firewall):
        return {
            **os.environ,
            "BACKEND_ROOT": str(backend),
            "UNIT_ROOT": str(units),
            "FIREWALL_FILE": str(firewall),
            "PRINTER_UPSTREAM": "192.168.1.30",
        }

    def test_prepare_scopes_proxy_listener_firewall_and_egress(self):
        with tempfile.TemporaryDirectory() as directory:
            backend, units, firewall = self.make_fixture(directory)
            result = subprocess.run(
                [str(SCRIPT), "--prepare-only"],
                env=self.environment(backend, units, firewall),
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            rules = firewall.read_text()
            self.assertIn('--dport 10631 -j ACCEPT', rules)
            self.assertLess(rules.index('--dport 10631'), rules.index('-j DROP'))

            socket_unit = (units / "hermes-dell-printer-proxy.socket").read_text()
            service_unit = (units / "hermes-dell-printer-proxy.service").read_text()
            self.assertIn("ListenStream=172.30.55.1:10631", socket_unit)
            self.assertIn("192.168.1.30:631", service_unit)

            squid = (backend / "squid.conf").read_text()
            self.assertIn("acl dell_printer dstdomain printer.agent.lan", squid)
            self.assertIn("acl dell_printer_ipp port 10631", squid)
            allow = "http_access allow dev_backend CONNECT dell_printer dell_printer_ipp"
            self.assertLess(squid.index(allow), squid.index("http_access deny private_networks"))
            self.assertNotIn("192.168.1.30", squid)

            compose = (backend / "compose.yaml").read_text()
            proxy_section = compose.split("  dev:", 1)[0]
            self.assertIn('"printer.agent.lan:172.30.55.1"', proxy_section)
            dev_section = compose.split("  dev:", 1)[1].split("networks:", 1)[0]
            self.assertNotIn("hermes-agent-lan", dev_section)

    def test_rollback_restores_all_mutated_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            backend, units, firewall = self.make_fixture(directory)
            env = self.environment(backend, units, firewall)
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
            self.assertEqual(firewall.read_text(), FIREWALL)
            self.assertFalse((units / "hermes-dell-printer-proxy.socket").exists())
            self.assertFalse((units / "hermes-dell-printer-proxy.service").exists())

    def test_runtime_recreation_reuses_local_images(self):
        script = SCRIPT.read_text()
        recreate = next(
            line for line in script.splitlines() if "up -d" in line and "egress-proxy dev" in line
        )
        self.assertIn("--no-build", recreate)
        self.assertNotIn("--build", recreate)

    def test_runtime_builds_proxy_policy_before_recreation(self):
        script = SCRIPT.read_text()
        build = script.index("build egress-proxy")
        recreate = script.index("up -d --no-build --force-recreate egress-proxy dev")
        self.assertLess(build, recreate)

    def test_rollback_rebuilds_restored_proxy_policy_before_recreation(self):
        script = ROLLBACK.read_text()
        build = script.index("build egress-proxy")
        recreate = script.index("up -d --no-build --force-recreate egress-proxy dev")
        self.assertLess(build, recreate)


if __name__ == "__main__":
    unittest.main()
