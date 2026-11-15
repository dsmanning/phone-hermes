from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "provision-camera-event-search-frigate-access.sh"
ROLLBACK_SCRIPT = ROOT / "rollback-camera-event-search-frigate-access.sh"


COMPOSE = """services:
  egress-proxy:
    image: squid
    networks:
      - dev-internal
      - egress
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
"""

SQUID = """http_port 3128
acl CONNECT method CONNECT
acl dev_backend src 172.21.0.10/32
acl private_networks dst 172.16.0.0/12
http_access deny private_networks
http_access deny !CONNECT
http_access deny all
"""


class CameraEventFrigateAccessProvisioningTests(unittest.TestCase):
    def test_prepare_routes_only_proxy_to_existing_frigate_policy_network(self):
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
            updated_compose = compose.read_text()
            self.assertNotIn("    dns:\n", updated_compose)
            self.assertIn("      - hermes-agent-lan\n", updated_compose)
            self.assertIn(
                "  hermes-agent-lan:\n    external: true\n    name: hermes-agent-lan\n",
                updated_compose,
            )
            dev_section = updated_compose.split("  dev:\n", 1)[1].split(
                "networks:\n", 1
            )[0]
            self.assertNotIn("hermes-agent-lan", dev_section)

            updated_squid = squid.read_text()
            allow = (
                "http_access allow dev_backend frigate_gateway "
                "frigate_http read_method"
            )
            self.assertIn("acl frigate_gateway dstdomain frigate.agent.lan", updated_squid)
            self.assertIn("acl frigate_http port 80", updated_squid)
            self.assertIn("acl read_method method GET HEAD", updated_squid)
            self.assertIn(
                "cache_peer 172.30.55.1 parent 80 0 no-query originserver "
                "name=frigate_policy",
                updated_squid,
            )
            self.assertIn(
                "cache_peer_access frigate_policy allow frigate_gateway",
                updated_squid,
            )
            self.assertIn("never_direct allow frigate_gateway", updated_squid)
            self.assertLess(
                updated_squid.index(allow),
                updated_squid.index("http_access deny private_networks"),
            )
            self.assertEqual(
                (backend / "compose.yaml.before-frigate-access").read_text(),
                COMPOSE,
            )
            self.assertEqual(
                (backend / "squid.conf.before-frigate-access").read_text(),
                SQUID,
            )

    def test_rollback_restores_compose_and_squid_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory)
            compose = backend / "compose.yaml"
            squid = backend / "squid.conf"
            compose.write_text(COMPOSE)
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
            self.assertEqual(compose.read_text(), COMPOSE)
            self.assertEqual(squid.read_text(), SQUID)

    def test_runtime_rebuilds_proxy_image_after_squid_configuration_change(self):
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
            self.assertIn(
                "up -d --build --force-recreate egress-proxy", invocations
            )
            self.assertIn("https://github.com/", invocations)

    def test_runtime_rollback_rebuilds_proxy_image_with_restored_squid_config(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            backend = Path(directory)
            (backend / "compose.yaml").write_text("modified compose\n")
            (backend / "squid.conf").write_text("modified squid\n")
            (backend / "compose.yaml.before-frigate-access").write_text(COMPOSE)
            (backend / "squid.conf.before-frigate-access").write_text(SQUID)
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
                "if args[:2] == ['docker', 'inspect']:\n"
                "    print('dev-internal egress ')\n"
            )
            sudo.chmod(0o755)

            result = subprocess.run(
                [str(ROLLBACK_SCRIPT)],
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
            self.assertIn(
                "up -d --build --force-recreate egress-proxy", log.read_text()
            )


if __name__ == "__main__":
    unittest.main()
