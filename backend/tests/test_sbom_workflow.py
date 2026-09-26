"""Фиксирует безопасный контракт workflow генерации SBOM."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SBOM_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "sbom.yml"


class SbomWorkflowTest(unittest.TestCase):
    def test_external_actions_are_pinned_to_full_commit_sha(self) -> None:
        workflow = self._read_workflow()
        references = re.findall(
            r"^\s+(?:-\s+)?uses: ([^\s#]+)", workflow, flags=re.MULTILINE
        )

        self.assertEqual(len(references), 7)
        for reference in references:
            with self.subTest(reference=reference):
                self.assertRegex(reference, r"^[^@]+@[0-9a-f]{40}$")

    def test_generators_are_pinned_and_publish_only_validated_files(self) -> None:
        workflow = self._read_workflow()

        self.assertIn("cyclonedx-bom==7.4.0", workflow)
        self.assertIn('"@cyclonedx/cyclonedx-npm": "6.0.1"', self._read_frontend_package())
        self.assertIn("./node_modules/.bin/cyclonedx-npm", workflow)
        self.assertIn("--package-lock-only", workflow)
        self.assertIn("--omit dev", workflow)
        self.assertIn("--flatten-components", workflow)
        self.assertIn("npm ci --ignore-scripts", workflow)
        self.assertEqual(workflow.count("scripts/validate_sbom.py"), 2)
        self.assertEqual(workflow.count("retention-days: 7"), 2)
        self.assertEqual(workflow.count("if-no-files-found: error"), 2)
        self.assertNotIn("continue-on-error", workflow)

    def test_workflow_uses_read_only_permissions(self) -> None:
        workflow = self._read_workflow()

        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("pull-requests: write", workflow)

    def _read_workflow(self) -> str:
        if not SBOM_WORKFLOW.is_file():
            self.skipTest("GitHub workflow is intentionally absent from the runtime image")
        return SBOM_WORKFLOW.read_text(encoding="utf-8")

    def _read_frontend_package(self) -> str:
        package_json = PROJECT_ROOT / "frontend" / "package.json"
        if not package_json.is_file():
            self.skipTest("frontend package is intentionally absent from the runtime image")
        return package_json.read_text(encoding="utf-8")
