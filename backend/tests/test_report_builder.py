import unittest
from datetime import UTC, datetime

from backend.app.analysis.runner import AnalyzerRegistration, run_analysis
from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    Evidence,
    MetricResult,
    Recommendation,
    RecommendationPriority,
    RepositoryRef,
)
from backend.app.reporting import build_report_payload, render_markdown_report
from backend.app.scoring.engine import ScoreLimit


class ReportBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        timestamp = datetime(2026, 9, 18, 12, 30, tzinfo=UTC)
        self.context = AnalysisContext(
            repository=RepositoryRef(
                "repo-42",
                "team",
                "platform-api",
                "https://sourcecraft.example/team/platform-api",
            ),
            commit_sha="abc123",
            analyzed_at=timestamp,
            period_start=timestamp,
            period_end=timestamp,
        )

    def test_builds_api_payload_with_score_details_and_facts(self) -> None:
        evidence = Evidence(
            source="sourcecraft-appsec",
            reference="scan-42",
            summary="Найдена открытая критическая уязвимость.",
            url="https://sourcecraft.example/scans/42",
        )
        recommendation = Recommendation(
            code="security-update-critical",
            priority=RecommendationPriority.P0,
            problem="Есть открытая критическая уязвимость.",
            action="Обновить уязвимую зависимость.",
            rationale="Критическая уязвимость ограничивает итоговый Score.",
            expected_effect="Ограничение будет снято после подтверждённого исправления.",
            expected_score_delta=30,
            evidence=(evidence,),
        )
        execution = run_analysis(
            self.context,
            (
                registration(
                    "security",
                    90,
                    metrics=(
                        MetricResult(
                            code="critical-open",
                            value=1,
                            normalized_score=90,
                            summary="Одна критическая уязвимость открыта.",
                            evidence=(evidence,),
                        ),
                    ),
                    recommendations=(recommendation,),
                ),
                registration("cicd", 90),
                registration("documentation", 90),
                registration("activity", 90),
                registration("issues", 90),
                registration("code_health", 90),
            ),
            score_limit=ScoreLimit(
                maximum_score=60,
                code="security-open-critical",
                summary="Есть подтверждённая открытая критическая AppSec-уязвимость.",
            ),
        )

        report = build_report_payload(execution, analysis_id="analysis-42")

        self.assertEqual(report["repository"]["name"], "team/platform-api")
        self.assertEqual(report["analysis"]["commitSha"], "abc123")
        self.assertEqual(report["analysis"]["coverage"], 1)
        self.assertEqual(report["score"], 60)
        self.assertEqual(report["scoreDetails"]["uncappedScore"], 90)
        self.assertEqual(report["scoreDetails"]["scoreLimit"]["maximumScore"], 60)
        security = report["categories"][0]
        self.assertEqual(security["code"], "security")
        self.assertEqual(security["weight"], 25)
        self.assertEqual(security["effectiveWeight"], 25)
        self.assertEqual(security["points"], 22.5)
        self.assertEqual(security["facts"][0]["evidence"][0]["source"], "sourcecraft-appsec")
        self.assertEqual(report["recommendations"][0]["expectedScoreDelta"], 30)

    def test_renders_markdown_for_a_preliminary_analysis(self) -> None:
        execution = run_analysis(self.context, (registration("activity", 80),))

        report = render_markdown_report(execution, analysis_id="analysis-43")

        self.assertIn("# Repo Health: team/platform-api", report)
        self.assertIn("**Repo Health Score: 80 / 100**", report)
        self.assertIn("Покрытие данных: 15 %.", report)
        self.assertIn("Предварительная оценка: да.", report)
        self.assertIn("### Безопасность", report)
        self.assertIn("Причина: `analyzer_not_configured`", report)

    def test_rejects_empty_analysis_identifier(self) -> None:
        execution = run_analysis(self.context, ())

        with self.assertRaisesRegex(ValueError, "analysis_id must not be empty"):
            build_report_payload(execution, analysis_id="  ")


def registration(
    category_code: str,
    score: float,
    *,
    metrics: tuple[MetricResult, ...] = (),
    recommendations: tuple[Recommendation, ...] = (),
) -> AnalyzerRegistration:
    def evaluate(_: AnalysisContext) -> CategoryResult:
        return CategoryResult(
            category=category_code,
            status=DataStatus.MEASURED,
            score=score,
            summary=f"Результат категории {category_code}.",
            metrics=metrics,
            recommendations=recommendations,
        )

    return AnalyzerRegistration(category_code, evaluate)