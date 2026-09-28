"""Статические тесты workflow сканирования Docker-образов."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "container-security.yml"
FULL_SHA_PATTERN = re.compile(r"uses:\s+[\w.-]+/[\w.-]+@([0-9a-f]{40})(?:\s|$)")


class ContainerSecurityWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Runtime Docker-образ намеренно не содержит CI-конфигурацию.
        # В полном checkout GitHub Actions файл есть, и тесты выполняются.
        if not WORKFLOW_PATH.is_file():
            raise unittest.SkipTest("workflow is not included in the runtime Docker image")
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_external_actions_are_pinned_to_full_commit_shas(self) -> None:
        uses_lines = [line.strip() for line in self.workflow.splitlines() if "uses:" in line]

        self.assertEqual(len(uses_lines), 2)
        for line in uses_lines:
            self.assertRegex(line, FULL_SHA_PATTERN)

    def test_workflow_has_read_only_repository_permissions(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)

    def test_push_scan_runs_only_after_changes_reach_main(self) -> None:
        self.assertIn("push:\n    branches:\n      - main", self.workflow)

    def test_backend_and_frontend_images_are_built(self) -> None:
        self.assertIn("dockerfile: backend/Dockerfile", self.workflow)
        self.assertIn("context: .", self.workflow)
        self.assertIn("dockerfile: frontend/Dockerfile", self.workflow)
        self.assertIn("dockerfile: frontend/Dockerfile.prod", self.workflow)
        self.assertIn("image: sourcecraft-repo-health-production-frontend:scan", self.workflow)
        self.assertIn("context: frontend", self.workflow)
        self.assertIn("fail-fast: false", self.workflow)

    def test_scan_blocks_fixable_high_and_critical_os_vulnerabilities(self) -> None:
        self.assertIn("trivy image", self.workflow)
        self.assertIn("--scanners vuln", self.workflow)
        self.assertIn("--pkg-types os", self.workflow)
        self.assertIn("--severity HIGH,CRITICAL", self.workflow)
        self.assertIn("--ignore-unfixed", self.workflow)
        self.assertIn("--exit-code 1", self.workflow)
        self.assertNotIn("continue-on-error", self.workflow)

    def test_scanner_and_job_have_time_limits(self) -> None:
        self.assertIn("timeout-minutes: 20", self.workflow)
        self.assertIn("--timeout 10m", self.workflow)


if __name__ == "__main__":
    unittest.main()
