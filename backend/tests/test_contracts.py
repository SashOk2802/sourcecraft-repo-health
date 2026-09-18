import unittest
from datetime import UTC, datetime

from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    Recommendation,
    RecommendationPriority,
    RepositoryRef,
)


class ContractsTest(unittest.TestCase):
    def test_measured_category_requires_score(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a score"):
            CategoryResult(
                category="documentation",
                status=DataStatus.MEASURED,
                score=None,
                summary="README was analyzed.",
            )

    def test_recommendation_delta_is_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected_score_delta must be between 0 and 100"):
            Recommendation(
                code="improve-ci",
                priority=RecommendationPriority.P1,
                problem="Падают прогоны.",
                action="Исправить прогоны.",
                rationale="Стабильность CI влияет на оценку.",
                expected_score_delta=101,
            )

    def test_context_keeps_analysis_snapshot(self) -> None:
        timestamp = datetime(2026, 9, 15, tzinfo=UTC)
        context = AnalysisContext(
            repository=RepositoryRef("repo-1", "team", "service"),
            commit_sha="abc123",
            analyzed_at=timestamp,
            period_start=timestamp,
            period_end=timestamp,
        )

        self.assertEqual(context.repository.repository_slug, "service")
        self.assertEqual(context.commit_sha, "abc123")
