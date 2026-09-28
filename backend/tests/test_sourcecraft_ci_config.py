"""Protect the required SourceCraft CI checks and its secret-free contract."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
CONFIG = ROOT / ".sourcecraft" / "ci.yaml"


class SourceCraftCiConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = CONFIG.read_text(encoding="utf-8")

    def test_runs_for_pull_requests_to_main_and_pushes_to_main(self) -> None:
        self.assertRegex(
            self.config,
            re.compile(
                r"pull_request:.*source_branches: \[\"\*\*\"\].*"
                r"target_branches: \[\"main\"\].*push:.*branches: \[\"main\"\]",
                re.DOTALL,
            ),
        )

    def test_runs_portable_quality_and_security_checks(self) -> None:
        required_commands = (
            "python -m unittest discover -s backend/tests -v",
            "ruff check backend --ignore EXE002",
            "npm run check",
            "npm test",
            "npm run build",
            "bandit -r backend/app",
            "python -m pip_audit --local --skip-editable",
            "npm audit --package-lock-only --audit-level=high",
        )
        for command in required_commands:
            with self.subTest(command=command):
                self.assertIn(command, self.config)

    def test_removes_checkout_credentials_before_running_pull_request_code(self) -> None:
        self.assertRegex(
            self.config,
            re.compile(
                r"quality-and-security:\n"
                r" {4}checkout:\n"
                r" {6}remove_credentials: true\n"
                r" {4}settings:"
            ),
        )

    def test_pins_images_and_does_not_embed_credentials_or_private_locations(self) -> None:
        image_lines = [
            line.strip() for line in self.config.splitlines() if line.strip().startswith("image:")
        ]
        self.assertEqual(len(image_lines), 4)
        self.assertTrue(all("@sha256:" in line for line in image_lines))

        forbidden = (
            "SOURCECRAFT_TOKEN",
            "secrets.",
            "Authorization:",
            "sourcecraft.dev/",
        )
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.config)


if __name__ == "__main__":
    unittest.main()
