"""Защищает обязательные проверки безопасности GitHub Actions."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECURITY_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "security.yml"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
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

    def test_ci_starts_a_clean_stack_and_always_removes_its_resources(self) -> None:
        workflow = self._read_ci_workflow()

        self.assertIn("name: Docker Compose", workflow)
        self.assertIn("docker compose -p repo-health-ci up --build --wait", workflow)
        self.assertIn("curl --fail --silent --show-error http://localhost:8000/health", workflow)
        self.assertIn("curl --fail --silent --show-error http://localhost:8000/api/v1/health", workflow)
        self.assertIn("curl --fail --silent --show-error http://localhost:8000/api/v1/methodology", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn(
            "docker compose -p repo-health-ci down --volumes --remove-orphans",
            workflow,
        )

    def test_ci_validates_production_compose_with_placeholders(self) -> None:
        workflow = self._read_ci_workflow()

        self.assertIn("name: Check production Compose configuration", workflow)
        self.assertIn("docker compose -f compose.production.yaml config --quiet", workflow)
        self.assertIn("SOURCECRAFT_TOKEN: ci-placeholder", workflow)
        self.assertNotIn("SOURCECRAFT_TOKEN: ${{ secrets.", workflow)

    def test_ci_boots_the_static_production_stack(self) -> None:
        workflow = self._read_ci_workflow()

        self.assertIn("name: Start production-mode static stack", workflow)
        self.assertIn(
            "docker compose -p repo-health-production-ci -f compose.production.yaml up --build --wait",
            workflow,
        )
        self.assertIn("http://127.0.0.1:5173/@vite/client", workflow)
        self.assertIn("http://127.0.0.1:5173/src/main.tsx", workflow)
        production_smoke = workflow.split("name: Start production-mode static stack", 1)[1].split(
            "name: Verify production-mode frontend and API", 1
        )[0]
        self.assertNotIn("SOURCECRAFT_TOKEN:", production_smoke)
        self.assertIn(
            "docker compose -p repo-health-production-ci -f compose.production.yaml down --volumes --remove-orphans",
            workflow,
        )

    def test_ci_runs_real_bounded_git_process_tree_tests_on_windows(self) -> None:
        workflow = self._read_ci_workflow()

        self.assertIn("name: Windows Git safety", workflow)
        self.assertIn("runs-on: windows-latest", workflow)
        self.assertIn("timeout-minutes: 10", workflow)
        self.assertIn(
            "backend.tests.test_personal_git_analysis.BoundedGitRepositoryTest",
            workflow,
        )
        self.assertNotIn("continue-on-error", workflow)

    def _read_security_workflow(self) -> str:
        if not SECURITY_WORKFLOW.is_file():
            self.skipTest("GitHub workflow is intentionally absent from the runtime image")
        return SECURITY_WORKFLOW.read_text(encoding="utf-8")

    def _read_ci_workflow(self) -> str:
        if not CI_WORKFLOW.is_file():
            self.skipTest("GitHub workflow is intentionally absent from the runtime image")
        return CI_WORKFLOW.read_text(encoding="utf-8")
