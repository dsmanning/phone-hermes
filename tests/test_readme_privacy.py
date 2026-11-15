"""Checks that README examples do not disclose local deployment details."""

import ipaddress
from pathlib import Path
import re
import unittest


class ReadmePrivacyTests(unittest.TestCase):
    def test_readme_contains_no_private_addresses_local_names_or_home_paths(self):
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        patterns = (
            r"\b[\w-]+\.(?:local|agent\.lan)\b",
            r"/(?:home|media)/[^/\s]+/",
        )
        for pattern in patterns:
            self.assertIsNone(re.search(pattern, readme, re.IGNORECASE), pattern)
        for value in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", readme):
            try:
                address = ipaddress.IPv4Address(value)
            except ipaddress.AddressValueError:
                continue
            parts = tuple(int(part) for part in str(address).split("."))
            self.assertFalse(
                parts[0] == 10
                or (parts[0] == 172 and 16 <= parts[1] <= 31)
                or parts[:2] == (192, 168),
                "README contains a private IPv4 address",
            )
