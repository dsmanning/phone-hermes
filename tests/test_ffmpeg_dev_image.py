from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "provision-hermes-dev-ffmpeg.sh"
ROLLBACK_SCRIPT = ROOT / "rollback-hermes-dev-ffmpeg.sh"


DOCKERFILE = """FROM debian:trixie-slim
RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
        ca-certificates \\
        git \\
    && rm -rf /var/lib/apt/lists/*
"""

DOCKERFILE_NO_CLEANUP = """FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git
"""


class HermesDevFfmpegProvisioningTests(unittest.TestCase):
    def test_prepare_adds_ffmpeg_to_existing_package_install(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory)
            dockerfile = backend / "Dockerfile"
            dockerfile.write_text(DOCKERFILE)

            result = subprocess.run(
                [str(SCRIPT), "--prepare-only"],
                env={**os.environ, "BACKEND_ROOT": str(backend)},
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            updated = dockerfile.read_text()
            self.assertIn("        ffmpeg \\\n", updated)
            self.assertEqual(updated.count("ffmpeg"), 1)
            self.assertEqual(
                (backend / "Dockerfile.before-ffmpeg").read_text(), DOCKERFILE
            )

    def test_prepare_handles_install_command_without_apt_lists_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory)
            dockerfile = backend / "Dockerfile"
            dockerfile.write_text(DOCKERFILE_NO_CLEANUP)

            result = subprocess.run(
                [str(SCRIPT), "--prepare-only"],
                env={**os.environ, "BACKEND_ROOT": str(backend)},
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            updated = dockerfile.read_text()
            self.assertIn(
                "apt-get install -y --no-install-recommends ffmpeg ", updated
            )
            self.assertEqual(updated.count("ffmpeg"), 1)

    def test_rollback_restores_original_dockerfile(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory)
            dockerfile = backend / "Dockerfile"
            backup = backend / "Dockerfile.before-ffmpeg"
            dockerfile.write_text("modified\n")
            backup.write_text(DOCKERFILE)

            result = subprocess.run(
                [str(ROLLBACK_SCRIPT), "--prepare-only"],
                env={**os.environ, "BACKEND_ROOT": str(backend)},
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(dockerfile.read_text(), DOCKERFILE)

    def test_runtime_rebuilds_development_image(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            backend = Path(directory)
            (backend / "Dockerfile").write_text(DOCKERFILE)
            (backend / ".runtime.env").write_text("TEST_ONLY=1\n")
            fake_bin = backend / "bin"
            fake_bin.mkdir()
            log = backend / "sudo.log"
            sudo = fake_bin / "sudo"
            sudo.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "with open(os.environ['FAKE_SUDO_LOG'], 'a') as stream:\n"
                "    stream.write(' '.join(sys.argv[1:]) + '\\n')\n"
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
            self.assertIn("up -d --build --force-recreate dev", log.read_text())


if __name__ == "__main__":
    unittest.main()
