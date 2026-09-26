import unittest

from backend.app.contracts import CategoryResult, DataStatus
from backend.app.scoring.engine import (
    CategoryContribution,
    ScoreLimit,
    ScoreSummary,
    calculate_score,
)


class ScoringEngineTest(unittest.TestCase):
    def test_all_measured_categories_produce_weighted_score(self) -> None:
        summary = calculate_score(
            (
                category("security", 80),
                category("cicd", 60),
                category("documentation", 90),
                category("activity", 100),
                category("issues", 0),
                category("code_health", 40),
            )
        )

        self.assertEqual(summary.score, 67)
        self.assertEqual(summary.uncapped_score, 67)
        self.assertEqual(summary.coverage, 1)
        self.assertEqual(summary.measured_weight, 100)
        self.assertEqual(summary.applicable_weight, 100)
        self.assertFalse(summary.is_preliminary)
        self.assertEqual(contribution(summary, "security").effective_weight, 25)
        self.assertEqual(contribution(summary, "security").points, 20)

    def test_unavailable_category_is_excluded_from_score_but_reduces_coverage(self) -> None:
        summary = calculate_score(
            (
                category("security", None, DataStatus.UNAVAILABLE),
                category("cicd", 58),
                category("documentation", 88),
                category("activity", 91),
                category("issues", 58),
                category("code_health", 70),
            )
        )

        self.assertAlmostEqual(summary.score, 73.4)
        self.assertAlmostEqual(summary.coverage, 0.75)
        self.assertEqual(summary.measured_weight, 75)
        self.assertEqual(summary.applicable_weight, 100)
        self.assertTrue(summary.is_preliminary)
        self.assertIsNone(contribution(summary, "security").points)
        self.assertAlmostEqual(contribution(summary, "cicd").effective_weight, 26.666666666666668)
        self.assertAlmostEqual(contribution(summary, "cicd").points, 15.466666666666667)

    def test_code_health_not_applicable_is_excluded_from_score_and_coverage(self) -> None:
        """Ноль поддерживаемых файлов (code_health NOT_APPLICABLE) не делает Score
        предварительным: категория выпадает и из Score, и из покрытия (4.1)."""
        summary = calculate_score(
            (
                category("security", 100),
                category("cicd", 100),
                category("documentation", 100),
                category("activity", 100),
                category("issues", 100),
                category("code_health", None, DataStatus.NOT_APPLICABLE),
            )
        )

        self.assertEqual(summary.score, 100)
        self.assertEqual(summary.coverage, 1)
        self.assertEqual(summary.measured_weight, 95)
        self.assertEqual(summary.applicable_weight, 95)
        self.assertFalse(summary.is_preliminary)
        self.assertIsNone(contribution(summary, "code_health").effective_weight)

    def test_not_applicable_category_is_excluded_from_coverage_denominator(self) -> None:
        summary = calculate_score(
            (
                category("security", 100),
                category("cicd", 100),
                category("documentation", 100),
                category("activity", 100),
                category("issues", None, DataStatus.NOT_APPLICABLE),
                category("code_health", 100),
            )
        )

        self.assertEqual(summary.score, 100)
        self.assertEqual(summary.coverage, 1)
        self.assertEqual(summary.measured_weight, 85)
        self.assertEqual(summary.applicable_weight, 85)
        self.assertFalse(summary.is_preliminary)
        self.assertIsNone(contribution(summary, "issues").effective_weight)

    def test_no_measured_categories_has_no_score(self) -> None:
        summary = calculate_score(
            tuple(
                category(code, None, DataStatus.UNAVAILABLE)
                for code in ("security", "cicd", "documentation", "activity", "issues", "code_health")
            )
        )

        self.assertIsNone(summary.score)
        self.assertIsNone(summary.uncapped_score)
        self.assertEqual(summary.coverage, 0)
        self.assertEqual(summary.measured_weight, 0)
        self.assertTrue(summary.is_preliminary)
        self.assertTrue(all(item.points is None for item in summary.categories))

    def test_all_not_applicable_has_no_score_or_coverage(self) -> None:
        summary = calculate_score(
            tuple(
                category(code, None, DataStatus.NOT_APPLICABLE)
                for code in ("security", "cicd", "documentation", "activity", "issues", "code_health")
            )
        )

        self.assertIsNone(summary.score)
        self.assertIsNone(summary.uncapped_score)
        self.assertIsNone(summary.coverage)
        self.assertEqual(summary.measured_weight, 0)
        self.assertEqual(summary.applicable_weight, 0)
        self.assertFalse(summary.is_preliminary)

    def test_confirmed_security_limit_caps_score(self) -> None:
        limit = ScoreLimit(
            maximum_score=60,
            code="security-open-critical",
            summary="Есть подтверждённая открытая критическая AppSec-уязвимость.",
        )
        summary = calculate_score(
            tuple(
                category(code, 90)
                for code in ("security", "cicd", "documentation", "activity", "issues", "code_health")
            ),
            score_limit=limit,
        )

        self.assertEqual(summary.uncapped_score, 90)
        self.assertEqual(summary.score, 60)
        self.assertEqual(summary.score_limit, limit)

    def test_security_limit_requires_measured_security_category(self) -> None:
        limit = ScoreLimit(
            maximum_score=60,
            code="security-open-critical",
            summary="Есть подтверждённая открытая критическая AppSec-уязвимость.",
        )

        with self.assertRaisesRegex(ValueError, "requires measured security"):
            calculate_score(
                (
                    category("security", None, DataStatus.UNAVAILABLE),
                    category("cicd", 90),
                    category("documentation", 90),
                    category("activity", 90),
                    category("issues", 90),
                    category("code_health", 90),
                ),
                score_limit=limit,
            )

    def test_categories_must_match_methodology(self) -> None:
        categories = (
            category("security", 90),
            category("cicd", 90),
            category("documentation", 90),
            category("activity", 90),
            category("issues", 90),
        )

        with self.assertRaisesRegex(ValueError, "missing: code_health"):
            calculate_score(categories)


def category(
    code: str,
    score: float | None,
    status: DataStatus = DataStatus.MEASURED,
) -> CategoryResult:
    return CategoryResult(
        category=code,
        status=status,
        score=score,
        summary=f"Результат категории {code}.",
    )


def contribution(summary: ScoreSummary, category_code: str) -> CategoryContribution:
    for item in summary.categories:
        if item.category == category_code:
            return item
    raise AssertionError(f"Категория {category_code} не найдена")
