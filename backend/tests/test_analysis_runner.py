import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from backend.app.analysis.runner import AnalyzerRegistration, run_analysis
from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    Recommendation,
    RecommendationPriority,
    RepositoryRef,
)
from backend.app.scoring.methodology import METHODOLOGY_VERSION


class AnalysisRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        timestamp = datetime(2026, 9, 18, tzinfo=UTC)
        self.context = AnalysisContext(
            repository=RepositoryRef("repo-42", "team", "platform-api"),
            commit_sha="abc123",
            analyzed_at=timestamp,
            period_start=timestamp,
            period_end=timestamp,
        )

    def test_runs_all_categories_and_returns_score_details(self) -> None:
        execution = run_analysis(
            self.context,
            (
                registration("security", 80),
                registration("cicd", 60),
                registration("documentation", 90),
                registration("activity", 100),
                registration("issues", 0),
                registration("code_health", 40),
            ),
        )

        self.assertEqual(execution.analysis.score, 67)
        self.assertEqual(execution.analysis.methodology_version, METHODOLOGY_VERSION)
        self.assertEqual(execution.score_summary.coverage, 1)
        self.assertEqual(
            tuple(category.category for category in execution.analysis.categories),
            ("security", "cicd", "documentation", "activity", "issues", "code_health"),
        )

    def test_missing_analyzers_produce_preliminary_result(self) -> None:
        execution = run_analysis(
            self.context,
            (registration("activity", 80),),
        )

        self.assertEqual(execution.analysis.score, 80)
        self.assertAlmostEqual(execution.score_summary.coverage, 0.15)
        self.assertTrue(execution.score_summary.is_preliminary)
        self.assertEqual(
            category_by_code(execution.analysis.categories, "security").status,
            DataStatus.UNAVAILABLE,
        )
        self.assertEqual(
            category_by_code(execution.analysis.categories, "security").reason,
            "analyzer_not_configured",
        )

    def test_analyzer_failure_does_not_stop_other_categories(self) -> None:
        def fail(_: AnalysisContext) -> CategoryResult:
            raise RuntimeError("source is unavailable")

        with patch("backend.app.analysis.runner.logger"):
            execution = run_analysis(
                self.context,
                (
                    AnalyzerRegistration("security", fail),
                    registration("cicd", 100),
                    registration("documentation", 100),
                    registration("activity", 100),
                    registration("issues", 100),
                    registration("code_health", 100),
                ),
            )

        self.assertEqual(execution.analysis.score, 100)
        self.assertAlmostEqual(execution.score_summary.coverage, 0.75)
        self.assertEqual(
            category_by_code(execution.analysis.categories, "security").status,
            DataStatus.ERROR,
        )
        self.assertEqual(
            category_by_code(execution.analysis.categories, "security").reason,
            "analyzer_execution_failed",
        )

    def test_analyzer_failure_is_logged_with_category_and_repository(self) -> None:
        def fail(_: AnalysisContext) -> CategoryResult:
            raise RuntimeError("source is unavailable")

        with patch("backend.app.analysis.runner.logger") as logger:
            run_analysis(self.context, (AnalyzerRegistration("security", fail),))

        logger.exception.assert_called_once_with(
            "Ошибка выполнения анализатора.",
            extra={"category": "security", "repository_id": "repo-42"},
        )

    def test_recommendations_are_deduplicated_and_sorted_by_priority(self) -> None:
        execution = run_analysis(
            self.context,
            (
                registration(
                    "security",
                    90,
                    recommendations=(recommendation("same-problem", RecommendationPriority.P2),),
                ),
                registration(
                    "cicd",
                    90,
                    recommendations=(
                        recommendation("same-problem", RecommendationPriority.P0),
                        recommendation("later-problem", RecommendationPriority.P3),
                    ),
                ),
                registration("documentation", 90),
                registration("activity", 90),
                registration("issues", 90),
                registration("code_health", 90),
            ),
        )

        self.assertEqual(
            tuple(item.code for item in execution.analysis.recommendations),
            ("same-problem", "later-problem"),
        )
        self.assertEqual(
            execution.analysis.recommendations[0].priority,
            RecommendationPriority.P0,
        )

    def test_duplicate_analyzer_registration_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate analyzer categories: activity"):
            run_analysis(
                self.context,
                (registration("activity", 80), registration("activity", 90)),
            )


def registration(
    category_code: str,
    score: float,
    *,
    recommendations: tuple[Recommendation, ...] = (),
) -> AnalyzerRegistration:
    def evaluate(_: AnalysisContext) -> CategoryResult:
        return CategoryResult(
            category=category_code,
            status=DataStatus.MEASURED,
            score=score,
            summary=f"Результат категории {category_code}.",
            recommendations=recommendations,
        )

    return AnalyzerRegistration(category_code, evaluate)


def recommendation(code: str, priority: RecommendationPriority) -> Recommendation:
    return Recommendation(
        code=code,
        priority=priority,
        problem=f"Проблема {code}.",
        action=f"Действие {code}.",
        rationale=f"Обоснование {code}.",
    )


def category_by_code(
    categories: tuple[CategoryResult, ...],
    category_code: str,
) -> CategoryResult:
    for category in categories:
        if category.category == category_code:
            return category
    raise AssertionError(f"Категория {category_code} не найдена")
