"""Behavior tests; model responses here are test doubles, never live evidence."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "default-image-review" / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("default_image_review_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingLlm:
    def __init__(self):
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text="TEST RESPONSE", model="test-default", provider="test-provider")


class ReviewTests(unittest.TestCase):
    def test_sends_pixels_to_configured_default_not_auxiliary(self):
        self.assertTrue(PLUGIN.exists(), "host-side image-review plugin is missing")
        plugin = load_plugin()
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "camera.jpg"
            image.write_bytes(b"\xff\xd8\xff\xe0test-image-payload\xff\xd9")
            llm = RecordingLlm()
            config = {"model": {"default": "test-default", "provider": "test-provider"},
                      "auxiliary": {"vision": {"model": "test-auxiliary"}}}
            ctx = SimpleNamespace(llm=llm, profile_name="test-profile",
                                  get_config=lambda key, default=None: [tmp] if key == "image_roots" else default)
            result = plugin.review(ctx, str(image), "What is visible?", config_reader=lambda: config)
            self.assertTrue(result["success"])
            self.assertEqual(result["requested_model"], "test-default")
            self.assertEqual(result["requested_provider"], "test-provider")
            self.assertEqual(result["reported_model"], "test-default")
            self.assertEqual(result["profile"], "test-profile")
            self.assertEqual(result["review"], "TEST RESPONSE")
            self.assertEqual(len(llm.calls), 1)
            call = llm.calls[0]
            self.assertEqual(call["model"], "test-default")
            self.assertEqual(call["provider"], "test-provider")
            self.assertNotIn("task", call)
            self.assertNotIn("profile", call)
            self.assertEqual(call["input"][0]["data"], image.read_bytes())
            self.assertEqual(call["input"][0]["mime_type"], "image/jpeg")
            self.assertEqual(config["auxiliary"]["vision"]["model"], "test-auxiliary")
            self.assertEqual(result["fallback_status"], "unknown-host-facade-does-not-expose-fallback-history")
            self.assertFalse(result["default_model_verified"])

    def test_only_bounded_regular_images_inside_explicit_roots_are_read(self):
        plugin = load_plugin()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "images"
            root.mkdir()
            outside = Path(tmp) / "outside.jpg"
            outside.write_bytes(b"\xff\xd8\xffprivate\xff\xd9")
            (root / "linked.jpg").symlink_to(outside)
            inside = root / "inside.jpg"
            inside.write_bytes(outside.read_bytes())
            (root / "internal-link.jpg").symlink_to(inside)
            (root / "not-image.jpg").write_bytes(b"not an image")
            (root / "oversize.jpg").write_bytes(b"\xff\xd8\xff" + b"x" * (10 * 1024 * 1024))
            import os
            os.mkfifo(root / "pipe.jpg")
            llm = RecordingLlm()
            ctx = SimpleNamespace(llm=llm, profile_name="test-profile",
                                  get_config=lambda key, default=None: [str(root)])
            cfg = lambda: {"model": {"default": "test-default", "provider": "test-provider"}}
            for path in (root / "internal-link.jpg", root / "linked.jpg", root / "not-image.jpg",
                         root / "oversize.jpg", root / "pipe.jpg", root, outside,
                         root / ".." / "outside.jpg", Path("relative.jpg")):
                with self.subTest(path=str(path)):
                    with self.assertRaises((ValueError, OSError)):
                        plugin.review(ctx, str(path), "Review", config_reader=cfg)
            self.assertEqual(llm.calls, [])
            for roots in ([], ["/"], [str(Path.home())], "not-a-list"):
                ctx.get_config = lambda key, default=None, roots=roots: roots
                with self.subTest(roots=roots):
                    with self.assertRaises(ValueError):
                        plugin.review(ctx, str(inside), "Review", config_reader=cfg)
            self.assertEqual(llm.calls, [])

    def test_invalid_selection_or_question_fails_before_model_call(self):
        plugin = load_plugin()
        llm = RecordingLlm()
        ctx = SimpleNamespace(llm=llm, profile_name="test", get_config=lambda *args: [])
        for cfg in ({}, {"model": "test-default"}, {"model": {"default": "", "provider": "test"}},
                    {"model": {"default": "test-default", "provider": "auto"}}):
            with self.subTest(config=cfg), self.assertRaises(ValueError):
                plugin.review(ctx, "/not/read.jpg", "Review", config_reader=lambda: cfg)
        cfg = {"model": {"default": "test-default", "provider": "test-provider"}}
        for question in ("", "  ", "x" * 8001, None):
            with self.subTest(question_type=type(question)), self.assertRaises(ValueError):
                plugin.review(ctx, "/not/read.jpg", question, config_reader=lambda: cfg)
        self.assertEqual(llm.calls, [])

    def test_route_mismatch_is_explicit_failure(self):
        plugin = load_plugin()
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "test.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nTEST")
            llm = RecordingLlm()
            ctx = SimpleNamespace(llm=llm, profile_name="test", get_config=lambda *args: [tmp])
            cfg = {"model": {"default": "another-default", "provider": "test-provider"}}
            result = plugin.review(ctx, str(image), "Review", config_reader=lambda: cfg)
            self.assertFalse(result["success"])
            self.assertEqual(result["error_code"], "reported-route-mismatch")
            self.assertEqual(result["requested_model"], "another-default")
            self.assertEqual(result["reported_model"], "test-default")
            self.assertEqual(llm.calls[0]["input"][0]["mime_type"], "image/png")
            self.assertNotIn("review", result)

    def test_registers_json_tool_and_operator_cli_with_sanitized_failures(self):
        plugin = load_plugin()
        self.assertTrue(callable(getattr(plugin, "register", None)), "plugin registration is missing")
        import argparse
        import contextlib
        import io
        import json
        from unittest.mock import patch
        tools, commands = [], []
        ctx = SimpleNamespace(register_tool=lambda **kw: tools.append(kw),
                              register_cli_command=lambda **kw: commands.append(kw))
        plugin.register(ctx)
        self.assertEqual(tools[0]["name"], "default_image_review")
        self.assertTrue(tools[0]["check_fn"]())
        with patch.object(plugin, "review", side_effect=RuntimeError("private-auth-detail")):
            reply = json.loads(tools[0]["handler"]({"image_path": "/tmp/test.jpg", "question": "Review"}))
            self.assertFalse(reply["success"])
            self.assertNotIn("private-auth-detail", json.dumps(reply))
        command = commands[0]
        self.assertEqual(command["name"], "review-image")
        parser = argparse.ArgumentParser()
        command["setup_fn"](parser)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.json"
            args = parser.parse_args(["--image", "/tmp/test.jpg", "--question", "Review", "--output", str(output)])
            with patch.object(plugin, "review", return_value={"success": True, "review": "TEST ONLY"}) as fn:
                with contextlib.redirect_stdout(io.StringIO()):
                    command["handler_fn"](args)
                self.assertEqual(json.loads(output.read_text())["review"], "TEST ONLY")
                self.assertEqual(fn.call_count, 1)
                with self.assertRaises(FileExistsError):
                    command["handler_fn"](args)
                self.assertEqual(fn.call_count, 1, "existing output must prevent another charged request")

    def test_installer_publishes_only_default_profile_plugin_and_runs_one_review(self):
        import json
        import os
        import subprocess
        script = PLUGIN.parents[1] / "install-default-image-review.sh"
        self.assertTrue(script.exists(), "host deployment script is missing")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            log = Path(tmp) / "calls.jsonl"
            hermes = bindir / "hermes"
            hermes.write_text("#!/usr/bin/env python3\nimport json,os,sys\n"
                              "with open(os.environ['CALL_LOG'],'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
                              "if sys.argv[1:]==['config','get','auxiliary.vision.model']: print('test-auxiliary')\n")
            hermes.chmod(0o700)
            env = {**os.environ, "HOME": str(home), "HERMES_HOME": str(home / ".hermes"),
                   "PATH": str(bindir) + os.pathsep + os.environ["PATH"], "CALL_LOG": str(log),
                   "FAKE_HERMES": str(hermes),
                   "BASH_FUNC_hermes%%": '() { python3 "$FAKE_HERMES" "$@"; }'}
            image_root = Path(tmp) / "images"
            image_root.mkdir()
            image = image_root / "camera.jpg"
            image.write_bytes(b"\xff\xd8\xffTEST\xff\xd9")
            args = ["bash", str(script), str(image_root), str(image), str(Path(tmp) / "review.json")]
            run = subprocess.run(args, env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            target = home / ".hermes" / "plugins" / "default-image-review"
            self.assertEqual((target / "__init__.py").read_bytes(), PLUGIN.read_bytes())
            manifest = json.loads((target / "plugin.yaml").read_text())
            self.assertEqual(manifest["provides_tools"], ["default_image_review"])
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(sum(call[0] == "review-image" for call in calls), 1)
            writes = [call for call in calls if call[:2] == ["config", "set"]]
            self.assertEqual({call[2] for call in writes}, {
                "plugins.entries.default-image-review.settings.image_roots",
                "plugins.entries.default-image-review.llm.allow_model_override",
                "plugins.entries.default-image-review.llm.allow_provider_override"})
            self.assertEqual(json.loads(writes[0][3]), [str(image_root)])
            self.assertEqual(calls.count(["config", "get", "auxiliary.vision.model"]), 2)
            # Refuse to overwrite an installed file with different content.
            (target / "__init__.py").write_text("user modification")
            run = subprocess.run(args, env=env, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual((target / "__init__.py").read_text(), "user modification")
            before = log.read_text()
            env["HERMES_HOME"] = str(home / ".hermes" / "profiles" / "another")
            run = subprocess.run(args, env=env, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(log.read_text(), before)


if __name__ == "__main__":
    unittest.main()
