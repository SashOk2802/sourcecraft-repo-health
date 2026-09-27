from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from backend.app.analyzers import code_health, documentation
from backend.app.contracts import (
    AnalysisContext,
    DataStatus,
    Recommendation,
    RecommendationPriority,
    RepositoryRef,
)
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
            {
                "total_analyzed_files",
                "todo_count",
                "fixme_count",
                "code_health.debt_file_ratio",
                "code_health.marker_age",
            },
        )

    def test_code_health_small_repo_single_marker_density_semantics(self) -> None:
        """Один FIXME не обнуляет категорию: знаменатель не меньше 10 файлов.

        penalty = (fixmes * 5 + todos) / max(total_files, DENSITY_MIN_FILES) * 100.
        Ниже пола score одинаковый. Выше пола он снова растёт с числом файлов.
        Два FIXME по-прежнему могут дать 0.
        """
        self.assertEqual(code_health.DENSITY_MIN_FILES, 10)
        context = analysis_context()

        def score_for(total_files: int, *, fixme_count: int = 1, todo_count: int = 0) -> float:
            result = code_health.evaluate(
                context,
                {
                    "total_files": total_files,
                    "todo_count": todo_count,
                    "fixme_count": fixme_count,
                    "files_with_debt": 1,
                },
            )
            assert result.score is not None
            return result.score

        self.assertEqual(score_for(1), 50.0)
        self.assertEqual(score_for(3), 50.0)
        self.assertEqual(score_for(6), 50.0)
        self.assertEqual(score_for(10), 50.0)
        self.assertEqual(score_for(100), 95.0)
        self.assertGreater(score_for(100), score_for(1))
        self.assertEqual(score_for(1, todo_count=1, fixme_count=0), 90.0)
        self.assertEqual(score_for(1, fixme_count=2), 0.0)

    def test_code_health_fixme_critical_count_boundary(self) -> None:
        """Граница FIXME_CRITICAL_COUNT = 2 зафиксирована тестом (V.2).

        Единичный FIXME — P2; от FIXME_CRITICAL_COUNT включительно рекомендация
        эскалируется до жёсткой P1 с формулировкой про опасный код.
        """
        self.assertEqual(code_health.FIXME_CRITICAL_COUNT, 2)
        context = analysis_context()

        def recommendation_for(fixme_count: int) -> Recommendation:
            result = code_health.evaluate(
                context,
                {
                    "total_files": 100,
                    "todo_count": 0,
                    "fixme_count": fixme_count,
                    "files_with_debt": 1,
                },
            )
            return next(
                r for r in result.recommendations if r.code == "code_health_resolve_fixme"
            )

        single = recommendation_for(code_health.FIXME_CRITICAL_COUNT - 1)
        self.assertEqual(single.priority, RecommendationPriority.P2)
        self.assertNotIn("опасный", single.rationale)

        at_threshold = recommendation_for(code_health.FIXME_CRITICAL_COUNT)
        self.assertEqual(at_threshold.priority, RecommendationPriority.P1)
        self.assertIn("опасный", at_threshold.rationale)

        above_threshold = recommendation_for(code_health.FIXME_CRITICAL_COUNT + 1)
        self.assertEqual(above_threshold.priority, RecommendationPriority.P1)
        self.assertIn("опасный", above_threshold.rationale)

    def test_file_exists_requires_a_real_file(self) -> None:
        """Отсутствующий путь внутри клона не считается найденным файлом."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (root / "docs").mkdir()
            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory

            self.assertTrue(repository.file_exists("README.md"))
            self.assertFalse(repository.file_exists("CONTRIBUTING.md"))
            self.assertFalse(repository.file_exists("docs"))
            self.assertFalse(repository.file_exists("../README.md"))

    def test_documentation_missing_readme_is_a_measured_deduction(self) -> None:
        """Пустой клон — измеренный плохой результат, а не ложные 100 баллов.

        Пропавшие регламенты дают штраф. Отдельной рекомендации за инструкции
        запуска нет: её причина совпадает с отсутствующим README.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory
            result = documentation.evaluate(
                analysis_context(),
                documentation.collect(repository),
            )

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 15)
        codes = {item.code for item in result.recommendations}
        self.assertIn("doc_missing_has_readme", codes)
        self.assertIn("doc_missing_has_contributing", codes)
        self.assertIn("doc_missing_has_license", codes)
        self.assertIn("doc_missing_has_codeowners", codes)
        self.assertNotIn("doc_missing_has_shortcuts", codes)


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