from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "provision-camera-event-search-workspace.sh"
ROLLBACK_SCRIPT = ROOT / "rollback-camera-event-search-workspace.sh"


class CameraEventWorkspaceProvisioningTests(unittest.TestCase):
    def test_prepare_adds_scoped_mounts_and_removes_shared_code_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = root / "backend"
            project = root / "code" / "camera-event-search"
            media = root / "minerva" / "Standard_Format"
            backend.mkdir()
            media.mkdir(parents=True)
            compose = backend / "compose.yaml"
            compose.write_text(
                "services:\n"
                "  dev:\n"
                "    volumes:\n"
                "      - ${PHONE_HERMES_REPO}:/workspace/phone-hermes\n"
                "      - /media/athena/code:/workspace/code\n"
            )

            result = subprocess.run(
                [str(SCRIPT), "--prepare-only"],
                env={
                    **os.environ,
                    "BACKEND_ROOT": str(backend),
                    "PROJECT_SOURCE": str(project),
                    "MEDIA_SOURCE": str(media),
                },
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(project.is_dir())
            updated = compose.read_text()
            self.assertIn(
                f"{project}:/workspace/camera-event-search", updated
            )
            self.assertIn(
                f"{media}:/workspace/camera-event-search/media:ro", updated
            )
            self.assertNotIn("/media/athena/code:/workspace/code", updated)
            self.assertEqual(
                (backend / "compose.yaml.before-camera-event-search").read_text(),
                "services:\n"
                "  dev:\n"
                "    volumes:\n"
                "      - ${PHONE_HERMES_REPO}:/workspace/phone-hermes\n"
                "      - /media/athena/code:/workspace/code\n",
            )

    def test_rollback_restores_compose_without_deleting_project_or_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = root / "backend"
            project = root / "code" / "camera-event-search"
            media = root / "minerva" / "Standard_Format"
            backend.mkdir()
            project.mkdir(parents=True)
            media.mkdir(parents=True)
            compose = backend / "compose.yaml"
            backup = backend / "compose.yaml.before-camera-event-search"
            compose.write_text("modified compose\n")
            backup.write_text("original compose\n")

            result = subprocess.run(
                [str(ROLLBACK_SCRIPT), "--prepare-only"],
                env={**os.environ, "BACKEND_ROOT": str(backend)},
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(compose.read_text(), "original compose\n")
            self.assertTrue(project.is_dir())
            self.assertTrue(media.is_dir())


if __name__ == "__main__":
    unittest.main()
