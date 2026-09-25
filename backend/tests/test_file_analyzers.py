from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from backend.app.analyzers import code_health, documentation
from backend.app.contracts import AnalysisContext, DataStatus, RepositoryRef
from backend.app.integrations.git_repository import LocalGitRepository


class FileAnalyzersTest(unittest.TestCase):
    def test_documentation_and_code_health_measure_prepared_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text(
                "# Demo\n\n## Запуск\n\n"
                + chr(96) * 3
                + "bash\npython -m pytest\n"
                + chr(96) * 3
                + "\n",
                encoding="utf-8",
            )
            (root / "CONTRIBUTING.md").write_text("# Правила\n", encoding="utf-8")
            (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
            (root / "CODEOWNERS").write_text("* @team\n", encoding="utf-8")
            (root / "service.py").write_text(
                "# TODO: add validation\n# FIXME: handle retry\n",
                encoding="utf-8",
            )

            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory
            context = analysis_context()

            documentation_result = documentation.evaluate(
                context,
                documentation.collect(repository),
            )
            code_health_result = code_health.evaluate(
                context,
                code_health.collect(repository),
            )

        self.assertEqual(documentation_result.status, DataStatus.MEASURED)
        self.assertEqual(documentation_result.score, 100)
        self.assertEqual(code_health_result.status, DataStatus.MEASURED)
        self.assertIsNotNone(code_health_result.score)
        self.assertLess(code_health_result.score, 100)
        self.assertEqual(
            {metric.code for metric in code_health_result.metrics},
            {"total_analyzed_files", "todo_count", "fixme_count"},
        )


def analysis_context() -> AnalysisContext:
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    return AnalysisContext(
        repository=RepositoryRef(
            id="repo-42",
            organization_slug="team",
            repository_slug="platform-api",
        ),
        commit_sha="main",
        analyzed_at=now,
        period_start=now,
        period_end=now,
    )
