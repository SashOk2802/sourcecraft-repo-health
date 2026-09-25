"""Защищает обязательные проверки безопасности GitHub Actions."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECURITY_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "security.yml"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


class CiSecurityWorkflowTest(unittest.TestCase):
    def test_external_actions_are_pinned_to_full_commit_sha(self) -> None:
        workflow = self._read_security_workflow()
        action_references = re.findall(r"^\s*- uses: ([^\s#]+)", workflow, flags=re.MULTILINE)

        self.assertGreaterEqual(len(action_references), 5)
        for reference in action_references:
            with self.subTest(reference=reference):
                self.assertRegex(reference, r"^[^@]+@[0-9a-f]{40}$")

    def test_dependency_audits_are_blocking_and_do_not_apply_fixes(self) -> None:
        workflow = self._read_security_workflow()

        self.assertIn("Python dependency audit", workflow)
        self.assertIn(".audit-venv/bin/python -m pip_audit --local --skip-editable", workflow)
        self.assertIn("Frontend dependency audit", workflow)
        self.assertIn("npm audit --package-lock-only --audit-level=high", workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertNotIn("--ignore-vuln", workflow)
        self.assertNotIn("npm audit fix", workflow)

    def test_audit_tool_is_pinned_in_the_isolated_workflow_environment(self) -> None:
        workflow = self._read_security_workflow()
        pyproject = PYPROJECT.read_text(encoding="utf-8")

        self.assertIn("python -m pip install pip==26.2.1", workflow)
        self.assertIn("pip-audit==2.10.1", workflow)
        self.assertNotIn("pip-audit", pyproject)

    def test_python_sast_is_pinned_blocking_and_scoped_to_application_code(self) -> None:
        workflow = self._read_security_workflow()
        pyproject = PYPROJECT.read_text(encoding="utf-8")

        self.assertIn("name: Python SAST", workflow)
        self.assertIn("bandit==1.9.4", workflow)
        self.assertIn(".sast-venv/bin/bandit -r backend/app", workflow)
        self.assertIn("--severity-level medium", workflow)
        self.assertIn("--confidence-level medium", workflow)
        self.assertIn("--ignore-nosec", workflow)
        self.assertNotIn("--exit-zero", workflow)
        self.assertNotIn("bandit", pyproject)

    def test_fixed_pytest_range_is_explicit(self) -> None:
        pyproject = PYPROJECT.read_text(encoding="utf-8")

        self.assertIn('"pytest>=9.0.3,<10.0"', pyproject)

    def test_secret_scan_does_not_publish_findings(self) -> None:
        workflow = self._read_security_workflow()

        self.assertIn('GITLEAKS_ENABLE_COMMENTS: "false"', workflow)
        self.assertIn('GITLEAKS_ENABLE_UPLOAD_ARTIFACT: "false"', workflow)
        self.assertIn('GITLEAKS_ENABLE_SUMMARY: "false"', workflow)

    def _read_security_workflow(self) -> str:
        if not SECURITY_WORKFLOW.is_file():
            self.skipTest("GitHub workflow is intentionally absent from the runtime image")
        return SECURITY_WORKFLOW.read_text(encoding="utf-8")
