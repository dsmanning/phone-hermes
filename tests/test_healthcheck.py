import json
from pathlib import Path
import tempfile
import unittest

from hermes_pbx_bridge.healthcheck import (
    CheckResult,
    classify_transition,
    load_state,
    save_state,
    validate_frigate_version,
)


class HealthTransitionTests(unittest.TestCase):
    def test_accepts_frigate_plain_text_version_response(self):
        self.assertTrue(validate_frigate_version("0.18.0-77a66e7\n"))
        self.assertFalse(validate_frigate_version(""))
        self.assertFalse(validate_frigate_version("<html>not a version</html>"))

    def test_first_healthy_run_establishes_silent_baseline(self):
        current = [
            CheckResult("frigate", True, "API version returned"),
            CheckResult("piper", True, "Wyoming info returned"),
        ]

        transition = classify_transition({}, current)

        self.assertEqual(transition.kind, "unchanged")
        self.assertEqual(transition.failed, ())
        self.assertEqual(transition.recovered, ())

    def test_reports_new_failure_once_then_suppresses_duplicate(self):
        previous = {"frigate": True, "piper": True}
        current = [
            CheckResult("frigate", False, "HTTP request failed"),
            CheckResult("piper", True, "Wyoming info returned"),
        ]

        first = classify_transition(previous, current)
        duplicate = classify_transition(
            {result.name: result.healthy for result in current}, current
        )

        self.assertEqual(first.kind, "failure")
        self.assertEqual(first.failed, ("frigate",))
        self.assertEqual(duplicate.kind, "unchanged")

    def test_reports_recovery(self):
        previous = {"frigate": False, "piper": True}
        current = [
            CheckResult("frigate", True, "API version returned"),
            CheckResult("piper", True, "Wyoming info returned"),
        ]

        transition = classify_transition(previous, current)

        self.assertEqual(transition.kind, "recovery")
        self.assertEqual(transition.recovered, ("frigate",))

    def test_state_round_trip_is_atomic_and_contains_no_details(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.json"
            results = [
                CheckResult("frigate", False, "sensitive diagnostic text"),
                CheckResult("piper", True, "Wyoming info returned"),
            ]

            save_state(path, results)

            self.assertEqual(load_state(path), {"frigate": False, "piper": True})
            stored = json.loads(path.read_text())
            self.assertEqual(stored, {"frigate": False, "piper": True})
            self.assertNotIn("sensitive", path.read_text())
            self.assertEqual(list(path.parent.glob("*.partial")), [])


if __name__ == "__main__":
    unittest.main()
