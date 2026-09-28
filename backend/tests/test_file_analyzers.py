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
from backend.app.integrations.git_repository import GitCloneError, LocalGitRepository


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

    def test_run_instruction_heading_may_start_with_the_keyword(self) -> None:
        """Заголовок «## Запуск» — инструкция, а «latest runtime» — нет."""
        with_heading = evaluate_documentation({"README.md": "# Demo\n\n## Запуск\n"})
        with_prefix = evaluate_documentation({"README.md": "# Demo\n\n## Как запустить проект\n"})
        false_positive = evaluate_documentation(
            {"README.md": "# Demo\n\nThe latest runtime is already documented.\n"}
        )
        with_command = evaluate_documentation(
            {"README.md": "# Demo\n\n```bash\npytest\n```\n"}
        )

        self.assertEqual(with_heading.score, 50)
        self.assertEqual(with_prefix.score, 50)
        self.assertNotIn("doc_missing_has_shortcuts", recommendation_codes(with_heading))
        self.assertEqual(false_positive.score, 35)
        self.assertIn("doc_missing_has_shortcuts", recommendation_codes(false_positive))
        self.assertEqual(with_command.score, 50)
        self.assertNotIn("doc_missing_has_shortcuts", recommendation_codes(with_command))

    def test_missing_regulations_are_a_low_score_not_missing_data(self) -> None:
        result = evaluate_documentation({})

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 15)
        self.assertEqual(
            recommendation_codes(result),
            {
                "doc_missing_has_readme",
                "doc_missing_has_contributing",
                "doc_missing_has_license",
                "doc_missing_has_codeowners",
            },
        )
        by_code = {item.code: item for item in result.recommendations}
        expected = {
            "doc_missing_has_readme": (RecommendationPriority.P1, documentation.PENALTY_README),
            "doc_missing_has_contributing": (
                RecommendationPriority.P2,
                documentation.PENALTY_CONTRIBUTING,
            ),
            "doc_missing_has_license": (RecommendationPriority.P2, documentation.PENALTY_LICENSE),
            "doc_missing_has_codeowners": (
                RecommendationPriority.P3,
                documentation.PENALTY_CODEOWNERS,
            ),
        }
        for code, (priority, penalty) in expected.items():
            recommendation = by_code[code]
            self.assertIs(recommendation.priority, priority)
            self.assertEqual(recommendation.expected_score_delta, penalty)
            self.assertEqual(recommendation.evidence[0].reference, "main")
            self.assertTrue(recommendation.evidence[0].summary)

    def test_missing_readme_does_not_add_a_second_instructions_penalty(self) -> None:
        result = evaluate_documentation({"CONTRIBUTING.md": "# Правила\n", "LICENSE": "MIT\n"})

        self.assertEqual(result.score, 50)
        self.assertNotIn("doc_missing_has_shortcuts", recommendation_codes(result))
        self.assertNotIn("has_shortcuts", {metric.code for metric in result.metrics})

    def test_alternate_regulation_paths_score_full_marks(self) -> None:
        result = evaluate_documentation(
            {
                "README.rst": "Demo\n\n## Testing\n",
                "CONTRIBUTING.md": "# Правила\n",
                "LICENSE.txt": "MIT\n",
                ".github/CODEOWNERS": "* @team\n",
            }
        )

        self.assertEqual(result.score, 100)
        self.assertEqual(result.recommendations, ())

    def test_documentation_error_fact_is_not_a_zero_score(self) -> None:
        result = documentation.evaluate(analysis_context(), {"error": "clone failed"})

        self.assertIs(result.status, DataStatus.ERROR)
        self.assertIsNone(result.score)
        self.assertEqual(result.reason, "clone failed")

    def test_file_exists_requires_a_real_file_inside_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text("ok\n", encoding="utf-8")
            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory

            (root / "docs").mkdir()
            (root / "docs" / "CODEOWNERS").mkdir()

            self.assertTrue(repository.file_exists("README.md"))
            self.assertFalse(repository.file_exists("LICENSE"))
            self.assertFalse(repository.file_exists("docs/CODEOWNERS"))
            self.assertFalse(repository.file_exists("../outside.txt"))

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").mkdir()
            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory

            self.assertFalse(repository.file_exists("README.md"))
            result = documentation.evaluate(analysis_context(), documentation.collect(repository))
            self.assertIn("doc_missing_has_readme", recommendation_codes(result))

    def test_documentation_collect_refuses_an_unprepared_workspace(self) -> None:
        repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")

        with self.assertRaises(GitCloneError):
            documentation.collect(repository)

    def test_code_health_small_repo_single_marker_density_semantics(self) -> None:
        """Одинокий FIXME в репозитории из 1–5 файлов обнуляет категорию.

        Плотность растёт как 1/total_files, поэтому один FIXME даёт penalty
        >= 100 при total_files <= 5. Это правило уже зафиксировано методикой v1.
        """
        context = analysis_context()

        def score_for(total_files: int) -> float:
            result = code_health.evaluate(
                context,
                {
                    "total_files": total_files,
                    "todo_count": 0,
                    "fixme_count": 1,
                    "files_with_debt": 1,
                },
            )
            assert result.score is not None
            return result.score

        self.assertEqual(score_for(1), 0.0)
        self.assertEqual(score_for(3), 0.0)
        self.assertAlmostEqual(score_for(6), 16.67, places=2)
        self.assertGreater(score_for(6), score_for(3))
        self.assertEqual(score_for(100), 95.0)

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

    def test_repository_without_source_files_is_not_applicable(self) -> None:
        result = evaluate_code_health({"README.md": "TODO: this is not source code\n"})

        self.assertIs(result.status, DataStatus.NOT_APPLICABLE)
        self.assertIsNone(result.score)
        self.assertEqual(result.recommendations, ())

    def test_vendor_and_dependencies_do_not_create_debt(self) -> None:
        result = evaluate_code_health(
            {
                "node_modules/left-pad.js": "// TODO: ignore\n// FIXME: ignore\n",
                "vendor/lib.go": "// TODO: ignore\n",
                "app.py": "print('ok')\n",
            }
        )

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 100)
        self.assertEqual(result.recommendations, ())
        todo_count = next(metric for metric in result.metrics if metric.code == "todo_count")
        total_files = next(metric for metric in result.metrics if metric.code == "total_analyzed_files")
        self.assertEqual(todo_count.value, 0)
        self.assertEqual(total_files.value, 1)

    def test_todo_recommendation_starts_above_fifteen_markers(self) -> None:
        self.assertEqual(code_health.TODO_RECOMMENDATION_THRESHOLD, 15)
        context = analysis_context()

        def result_for(todo_count: int):
            return code_health.evaluate(
                context,
                {
                    "total_files": 100,
                    "todo_count": todo_count,
                    "fixme_count": 0,
                    "files_with_debt": 1,
                },
            )

        at_threshold = result_for(15)
        above_threshold = result_for(16)

        self.assertEqual(recommendation_codes(at_threshold), set())
        self.assertEqual(recommendation_codes(above_threshold), {"code_health_clear_todos"})
        self.assertIs(above_threshold.recommendations[0].priority, RecommendationPriority.P3)

    def test_scanned_todo_comments_follow_the_fifteen_marker_boundary(self) -> None:
        def source(count: int) -> str:
            return "".join(f"# TODO: item {index}\n" for index in range(count))

        at_threshold = evaluate_code_health({"src/app.py": source(15)})
        above_threshold = evaluate_code_health({"src/app.py": source(16)})

        self.assertEqual(recommendation_codes(at_threshold), set())
        self.assertEqual(_metric_value(at_threshold, "todo_count"), 15)
        self.assertEqual(recommendation_codes(above_threshold), {"code_health_clear_todos"})
        self.assertIs(above_threshold.recommendations[0].priority, RecommendationPriority.P3)
        self.assertEqual(_metric_value(above_threshold, "todo_count"), 16)

    def test_fixme_evidence_points_at_the_source_line(self) -> None:
        result = evaluate_code_health({"src/app.py": "x = 1\n# FIXME: retry\n"})
        recommendation = result.recommendations[0]

        self.assertEqual(recommendation.code, "code_health_resolve_fixme")
        self.assertEqual(recommendation.evidence[0].reference, "src/app.py:2")

    def test_code_health_error_fact_is_not_a_zero_score(self) -> None:
        result = code_health.evaluate(analysis_context(), {"error": "clone failed"})

        self.assertIs(result.status, DataStatus.ERROR)
        self.assertIsNone(result.score)
        self.assertEqual(result.reason, "clone failed")

    def test_code_health_collect_refuses_an_unprepared_workspace(self) -> None:
        repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")

        with self.assertRaises(GitCloneError):
            code_health.collect(repository)


def evaluate_documentation(files: dict[str, str]):
    with tempfile.TemporaryDirectory() as temporary_directory:
        repository = repository_from_files(temporary_directory, files)
        return documentation.evaluate(analysis_context(), documentation.collect(repository))


def evaluate_code_health(files: dict[str, str]):
    with tempfile.TemporaryDirectory() as temporary_directory:
        repository = repository_from_files(temporary_directory, files)
        return code_health.evaluate(analysis_context(), code_health.collect(repository))


def recommendation_codes(result) -> set[str]:
    return {recommendation.code for recommendation in result.recommendations}


def _metric_value(result, code: str) -> float | int | str | None:
    return next(metric.value for metric in result.metrics if metric.code == code)


def repository_from_files(temporary_directory: str, files: dict[str, str]) -> LocalGitRepository:
    root = Path(temporary_directory)
    for relative, content in files.items():
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
    repository.temp_dir = temporary_directory
    return repository


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