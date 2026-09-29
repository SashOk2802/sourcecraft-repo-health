"""Bus factor и качество review: бонусные insights вне Score v2."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from backend.app.analysis.runner import AnalyzerRegistration, run_analysis
from backend.app.analyzers.activity import (
    ActivityFacts,
    CommitHistoryFacts,
    PullFact,
)
from backend.app.analyzers.collaboration import (
    MergeCheckFact,
    build_collaboration_insights,
    collect_merge_checks,
    evaluate_bus_factor,
    evaluate_review_quality,
)
from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    InsightResult,
    RepositoryRef,
)
from backend.app.integrations.git_repository import CommitRecord
from backend.app.integrations.sourcecraft import SourceCraftClientError
from backend.app.reporting import build_report_payload, render_markdown_report

ANALYZED_AT = datetime(2026, 9, 18, 12, tzinfo=UTC)
PERIOD_START = ANALYZED_AT - timedelta(days=180)
CONTEXT = AnalysisContext(
    repository=RepositoryRef("repo-1", "team", "platform", "https://sourcecraft.dev/team/platform"),
    commit_sha="a" * 40,
    analyzed_at=ANALYZED_AT,
    period_start=PERIOD_START,
    period_end=ANALYZED_AT,
)


def _commit(author: str, *, days_ago: int = 1, parents: int = 1) -> CommitRecord:
    return CommitRecord(
        committed_at=ANALYZED_AT - timedelta(days=days_ago),
        author_email=author,
        parent_count=parents,
    )


def _pull(slug: str, *, days_ago: int = 1) -> PullFact:
    moment = ANALYZED_AT - timedelta(days=days_ago)
    return PullFact(
        slug=slug,
        title=f"PR {slug}",
        status="merged",
        created_at=moment - timedelta(hours=1),
        updated_at=moment,
    )


class BusFactorTest(unittest.TestCase):
    def test_single_author_is_one(self) -> None:
        insight = evaluate_bus_factor([_commit("a@x"), _commit("a@x"), _commit("a@x")])

        self.assertEqual(insight.status, DataStatus.MEASURED)
        self.assertEqual(insight.value, 1)
        self.assertIsNotNone(insight.action)
        self.assertNotIn("@", insight.summary)
        self.assertNotIn("a@x", insight.detail or "")

    def test_two_equal_authors_cover_half_with_one(self) -> None:
        insight = evaluate_bus_factor([_commit("a@x"), _commit("b@x")])

        self.assertEqual(insight.value, 1)

    def test_three_equal_authors_need_two(self) -> None:
        insight = evaluate_bus_factor(
            [_commit("a@x"), _commit("b@x"), _commit("c@x")]
        )

        self.assertEqual(insight.value, 2)

    def test_merge_commits_do_not_count_toward_bus_factor(self) -> None:
        insight = evaluate_bus_factor(
            [
                _commit("a@x"),
                _commit("a@x", parents=2),
                _commit("b@x"),
                _commit("b@x"),
            ]
        )

        # Без merge: a=1, b=2 → половина покрыта одним автором (b).
        self.assertEqual(insight.value, 1)

    def test_truncated_history_is_insufficient(self) -> None:
        insight = evaluate_bus_factor(
            [_commit("a@x")],
            history_truncated=True,
        )

        self.assertEqual(insight.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(insight.value)

    def test_history_error_is_unavailable(self) -> None:
        insight = evaluate_bus_factor(
            (),
            history_error="commit_history_unavailable",
        )

        self.assertEqual(insight.status, DataStatus.UNAVAILABLE)

    def test_empty_period_is_not_applicable(self) -> None:
        insight = evaluate_bus_factor(())

        self.assertEqual(insight.status, DataStatus.NOT_APPLICABLE)


class ReviewQualityTest(unittest.TestCase):
    def test_all_approved_is_one(self) -> None:
        insight = evaluate_review_quality(
            (
                MergeCheckFact("1", False, 2),
                MergeCheckFact("2", False, 1),
            ),
            merged_in_period=2,
        )

        self.assertEqual(insight.status, DataStatus.MEASURED)
        self.assertEqual(insight.value, 1.0)

    def test_half_approved(self) -> None:
        insight = evaluate_review_quality(
            (
                MergeCheckFact("1", False, 1),
                MergeCheckFact("2", False, 0),
            ),
            merged_in_period=2,
        )

        self.assertEqual(insight.value, 0.5)

    def test_disabled_review_is_not_applicable(self) -> None:
        insight = evaluate_review_quality(
            (
                MergeCheckFact("1", True, None),
                MergeCheckFact("2", True, 0),
            ),
            merged_in_period=2,
        )

        self.assertEqual(insight.status, DataStatus.NOT_APPLICABLE)

    def test_http_error_is_unavailable_not_zero(self) -> None:
        insight = evaluate_review_quality(
            (MergeCheckFact("1", False, None, error="HTTP 500"),),
            merged_in_period=1,
        )

        self.assertEqual(insight.status, DataStatus.UNAVAILABLE)
        self.assertIsNone(insight.value)

    def test_missing_approves_on_merged_mr_is_unavailable(self) -> None:
        insight = evaluate_review_quality(
            (MergeCheckFact("1", False, None),),
            merged_in_period=1,
        )

        self.assertEqual(insight.status, DataStatus.UNAVAILABLE)

    def test_truncated_pulls_are_insufficient(self) -> None:
        insight = evaluate_review_quality(
            (),
            pulls_truncated=True,
            merged_in_period=5,
        )

        self.assertEqual(insight.status, DataStatus.INSUFFICIENT_SAMPLE)

    def test_sample_limit_notes_larger_population(self) -> None:
        checks = tuple(
            MergeCheckFact(str(index), False, 1) for index in range(20)
        )
        insight = evaluate_review_quality(
            checks,
            merged_in_period=40,
            sample_limit=20,
        )

        self.assertEqual(insight.value, 1.0)
        self.assertIn("20 из 40", insight.summary)

    def test_collect_merge_checks_caps_sample_and_keeps_errors(self) -> None:
        client = Mock()
        client.get_json.side_effect = [
            {"code_review": {"disabled": False, "total_approves": 1}},
            SourceCraftClientError("HTTP 503"),
        ]
        pulls = [_pull(str(index), days_ago=index) for index in range(1, 4)]

        checks = collect_merge_checks(client, CONTEXT.repository, pulls, CONTEXT, sample_limit=2)

        self.assertEqual(len(checks), 2)
        self.assertEqual(checks[0].total_approves, 1)
        self.assertIsNotNone(checks[1].error)
        self.assertEqual(client.get_json.call_count, 2)


class CollaborationInsightsWireTest(unittest.TestCase):
    def test_insights_do_not_change_score(self) -> None:
        def activity_with_insights(_: AnalysisContext) -> CategoryResult:
            return CategoryResult(
                category="activity",
                status=DataStatus.MEASURED,
                score=80,
                summary="Activity measured.",
                insights=(
                    InsightResult(
                        code="bus_factor",
                        label="Bus factor",
                        status=DataStatus.MEASURED,
                        value=1,
                        summary="Bus factor 1.",
                    ),
                ),
            )

        def activity_plain(_: AnalysisContext) -> CategoryResult:
            return CategoryResult(
                category="activity",
                status=DataStatus.MEASURED,
                score=80,
                summary="Activity measured.",
            )

        with_insights = run_analysis(
            CONTEXT,
            (AnalyzerRegistration("activity", activity_with_insights),),
        )
        without = run_analysis(
            CONTEXT,
            (AnalyzerRegistration("activity", activity_plain),),
        )

        self.assertEqual(with_insights.score_summary.score, without.score_summary.score)
        self.assertEqual(len(with_insights.analysis.insights), 1)
        self.assertEqual(without.analysis.insights, ())
        report = build_report_payload(with_insights, analysis_id="analysis-insights")
        self.assertEqual(report["score"], 80)
        self.assertEqual(report["insights"][0]["code"], "bus_factor")
        markdown = render_markdown_report(with_insights, analysis_id="analysis-insights")
        self.assertIn("## Сопровождение", markdown)
        self.assertIn("в Repo Health Score не входят", markdown)

    def test_build_insights_from_activity_facts(self) -> None:
        facts = ActivityFacts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            pulls=(_pull("1"),),
            commit_history=CommitHistoryFacts(
                collected=True,
                committed_at=(ANALYZED_AT - timedelta(days=1),),
                commits=(_commit("solo@x"),),
            ),
        )

        insights = build_collaboration_insights(
            facts,
            CONTEXT,
            merge_checks=(MergeCheckFact("1", False, 1),),
        )

        by_code = {item.code: item for item in insights}
        self.assertEqual(by_code["bus_factor"].value, 1)
        self.assertEqual(by_code["review_quality"].value, 1.0)


if __name__ == "__main__":
    unittest.main()
