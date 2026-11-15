from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "provision-homeassistant-gateway-access.sh"
ROLLBACK_SCRIPT = ROOT / "rollback-homeassistant-gateway-access.sh"


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
acl dev_backend src 172.21.0.10/32
acl frigate_gateway dstdomain frigate.agent.lan
acl frigate_http port 80
acl read_method method GET HEAD
cache_peer 172.30.55.1 parent 80 0 no-query originserver name=frigate_policy
cache_peer_access frigate_policy allow frigate_gateway
cache_peer_access frigate_policy deny all
never_direct allow frigate_gateway
acl private_networks dst 172.16.0.0/12
http_access allow dev_backend frigate_gateway frigate_http read_method
http_access deny private_networks
http_access deny !CONNECT
http_access deny all
"""


class HomeAssistantGatewayAccessProvisioningTests(unittest.TestCase):
    def test_prepare_adds_only_scoped_homeassistant_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory)
            compose = backend / "compose.yaml"
            squid = backend / "squid.conf"
            compose.write_text(COMPOSE)
            squid.write_text(SQUID)

            result = subprocess.run(
                [str(SCRIPT), "--prepare-only"],
                env={**os.environ, "BACKEND_ROOT": str(backend)},
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(compose.read_text(), COMPOSE)
            updated = squid.read_text()
            self.assertIn(
                "acl homeassistant_gateway dstdomain homeassistant.agent.lan", updated
            )
            self.assertIn("acl homeassistant_http port 80", updated)
            self.assertIn("acl homeassistant_method method GET HEAD POST", updated)
            self.assertIn(
                "http_upgrade_request_protocols websocket allow homeassistant_gateway",
                updated,
            )
            self.assertIn("http_upgrade_request_protocols OTHER deny all", updated)
            self.assertIn(
                "cache_peer 172.30.55.1 parent 80 0 no-query originserver "
                "name=homeassistant_policy",
                updated,
            )
            self.assertIn(
                "cache_peer_access homeassistant_policy allow homeassistant_gateway",
                updated,
            )
            self.assertIn("never_direct allow homeassistant_gateway", updated)
            allow = (
                "http_access allow dev_backend homeassistant_gateway "
                "homeassistant_http homeassistant_method"
            )
            self.assertLess(updated.index(allow), updated.index("http_access deny private_networks"))
            self.assertNotIn("192.168.1.68", updated)
            self.assertEqual(
                (backend / "squid.conf.before-homeassistant-access").read_text(),
                SQUID,
            )

    def test_migrates_case_sensitive_websocket_protocol_from_failed_rollout(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory)
            (backend / "compose.yaml").write_text(COMPOSE)
            squid = backend / "squid.conf"
            squid.write_text(SQUID)
            initial = subprocess.run(
                [str(SCRIPT), "--prepare-only"],
                env={**os.environ, "BACKEND_ROOT": str(backend)},
                text=True,
                capture_output=True,
            )
            self.assertEqual(initial.returncode, 0, initial.stderr)
            squid.write_text(
                squid.read_text().replace(
                    "http_upgrade_request_protocols websocket allow homeassistant_gateway",
                    "http_upgrade_request_protocols WebSocket allow homeassistant_gateway",
                )
            )

            result = subprocess.run(
                [str(SCRIPT), "--prepare-only"],
                env={**os.environ, "BACKEND_ROOT": str(backend)},
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            updated = squid.read_text()
            self.assertIn(
                "http_upgrade_request_protocols websocket allow homeassistant_gateway",
                updated,
            )
            self.assertNotIn(
                "http_upgrade_request_protocols WebSocket allow homeassistant_gateway",
                updated,
            )

    def test_rollback_restores_squid_and_preserves_existing_frigate_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory)
            (backend / "compose.yaml").write_text(COMPOSE)
            squid = backend / "squid.conf"
            squid.write_text(SQUID)
            prepare = subprocess.run(
                [str(SCRIPT), "--prepare-only"],
                env={**os.environ, "BACKEND_ROOT": str(backend)},
                text=True,
                capture_output=True,
            )
            self.assertEqual(prepare.returncode, 0, prepare.stderr)

            result = subprocess.run(
                [str(ROLLBACK_SCRIPT), "--prepare-only"],
                env={**os.environ, "BACKEND_ROOT": str(backend)},
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(squid.read_text(), SQUID)
            self.assertIn("name=frigate_policy", squid.read_text())
            self.assertNotIn("homeassistant_policy", squid.read_text())

    def test_runtime_rebuilds_proxy_and_runs_end_to_end_checks(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            backend = Path(directory)
            (backend / "compose.yaml").write_text(COMPOSE)
            (backend / "squid.conf").write_text(SQUID)
            (backend / ".runtime.env").write_text("TEST_ONLY=1\n")
            fake_bin = backend / "bin"
            fake_bin.mkdir()
            log = backend / "sudo.log"
            sudo = fake_bin / "sudo"
            sudo.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['FAKE_SUDO_LOG'], 'a') as stream:\n"
                "    stream.write(' '.join(args) + '\\n')\n"
                "joined = ' '.join(args)\n"
                "if 'docker network inspect' in joined:\n"
                "    print('true' if '.Internal' in joined else 'false')\n"
                "elif args[:2] == ['docker', 'inspect']:\n"
                "    print('dev-internal ')\n"
            )
            sudo.chmod(0o755)

            result = subprocess.run(
                [str(SCRIPT)],
                env={
                    **os.environ,
                    "BACKEND_ROOT": str(backend),
                    "FAKE_SUDO_LOG": str(log),
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                },
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            invocations = log.read_text()
            self.assertIn("up -d --build --force-recreate egress-proxy", invocations)
            self.assertIn("squid -k parse -f /etc/squid/squid.conf", invocations)
            self.assertIn("homeassistant.agent.lan/api/", invocations)
            self.assertIn("homeassistant.agent.lan/api/states", invocations)
            self.assertIn("homeassistant.agent.lan/api/conversation/process", invocations)
            self.assertNotIn("homeassistant.agent.lan/api/template", invocations)
            self.assertIn("homeassistant.agent.lan/api/websocket", invocations)
            self.assertIn("-X PUT", invocations)
            self.assertIn("frigate.agent.lan/api/version", invocations)
            self.assertIn("https://github.com/", invocations)


if __name__ == "__main__":
    unittest.main()
