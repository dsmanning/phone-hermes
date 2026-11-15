"""Read-only collector tests; all host trees below are temporary fixtures."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "collect-image-routing-host.py"


class CollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("image_collector", SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_collects_only_explicit_runtime_source_and_standards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "hermes-agent"
            source = install / "hermes_cli" / "plugins" / "context.py"
            source.parent.mkdir(parents=True)
            source.write_text("class PluginContext: pass\n")
            (install / "config.yaml").write_text("api_key: NEVER_EXPORT\n")
            (install / ".env").write_text("TOKEN=NEVER_EXPORT\n")
            standards = root / "default-project"
            standards.mkdir()
            (standards / "AGENTS.md").write_text("Use tests first.\n")
            (standards / "secret.txt").write_text("NEVER_EXPORT")
            report = self.module.collect(install, standards)
            self.assertEqual(report["sources"]["hermes_cli/plugins/context.py"], source.read_text())
            self.assertEqual(report["standards"]["AGENTS.md"], "Use tests first.\n")
            self.assertNotIn("NEVER_EXPORT", str(report))
            self.assertEqual(source.read_text(), "class PluginContext: pass\n")

    def test_rejects_source_symlinks_outside_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "hermes-agent"
            source = install / "hermes_cli" / "plugins.py"
            source.parent.mkdir(parents=True)
            secret = root / "secret.py"
            secret.write_text("NEVER_EXPORT")
            source.symlink_to(secret)
            report = self.module.collect(install, root / "absent")
            self.assertEqual(report["sources"], {})
            self.assertNotIn("NEVER_EXPORT", str(report))

    def test_collects_host_owned_llm_implementation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "agent" / "plugin_llm.py"
            source.parent.mkdir()
            source.write_text("class PluginLlm: pass\n")
            report = self.module.collect(root, root / "absent")
            self.assertEqual(report["sources"].get("agent/plugin_llm.py"), source.read_text())

    def test_missing_runtime_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.module.collect(Path(directory) / "absent", Path(directory) / "standards")
            self.assertFalse(report["install_exists"])
            self.assertEqual(report["sources"], {})
            self.assertTrue(report["warnings"])

    def test_oversized_files_are_not_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "hermes_cli" / "plugins.py"
            source.parent.mkdir()
            source.write_text("x" * (self.module.MAX_FILE_BYTES + 1))
            report = self.module.collect(root, root / "absent")
            self.assertEqual(report["sources"], {})
            self.assertTrue(any("size" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
