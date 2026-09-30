import unittest
from uuid import UUID

from hermes_pbx_bridge.security import ConnectionGuard


class ConnectionGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = ConnectionGuard({"10.1.30.20"})

    def test_rejects_source_outside_explicit_allowlist(self):
        with self.assertRaisesRegex(PermissionError, "source address"):
            self.guard.authorize_source("10.1.30.21")

    def test_accepts_configured_source(self):
        self.guard.authorize_source("10.1.30.20")

    def test_parses_nonzero_16_byte_call_uuid(self):
        expected = UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4")

        actual = self.guard.parse_call_uuid(expected.bytes)

        self.assertEqual(actual, expected)

    def test_rejects_malformed_or_nil_call_uuid(self):
        for payload in (b"short", bytes(16)):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "call UUID"):
                    self.guard.parse_call_uuid(payload)


if __name__ == "__main__":
    unittest.main()
