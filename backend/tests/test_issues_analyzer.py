from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.analyzers.issues import (
    STALE_AFTER_DAYS,
    IssuesFacts,
    build_facts,
    evaluate,
)
from backend.app.contracts import AnalysisContext, DataStatus, RecommendationPriority, RepositoryRef

ANALYZED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
PERIOD_START = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def context() -> AnalysisContext:
    return AnalysisContext(
        repository=RepositoryRef(
            id="repo-1",
            organization_slug="team",
            repository_slug="platform",
            web_url="https://sourcecraft.dev/team/platform",
        ),
        commit_sha="0" * 40,
        analyzed_at=ANALYZED_AT,
        period_start=PERIOD_START,
        period_end=ANALYZED_AT,
    )


def moment(days_before_analysis: int) -> str:
    return (ANALYZED_AT - timedelta(days=days_before_analysis)).isoformat()


def raw_issue(
    slug: str,
    *,
    created_days_ago: int,
    updated_days_ago: int,
    completed_days_ago: int | None = None,
    status_type: str = "completed",
) -> dict[str, object]:
    return {
        "slug": slug,
        "title": f"Задача {slug}",
        "created_at": moment(created_days_ago),
        "updated_at": moment(updated_days_ago),
        "completed_at": None if completed_days_ago is None else moment(completed_days_ago),
        "status": {"status_type": status_type},
    }


def open_issue(slug: str, *, created_days_ago: int, updated_days_ago: int) -> dict[str, object]:
    return raw_issue(
        slug,
        created_days_ago=created_days_ago,
        updated_days_ago=updated_days_ago,
        status_type="initial",
    )


class IssuesEvaluateTest(unittest.TestCase):
    """Проверяет методику категории Issues на подготовленных фактах, без сети."""

    def test_healthy_project_is_measured_with_expected_score(self) -> None:
        open_items = [
            open_issue(f"open-{index}", created_days_ago=40, updated_days_ago=5)
            for index in range(3)
        ]
        closed_items = [
            raw_issue(
                f"closed-{index}",
                created_days_ago=60,
                updated_days_ago=60 - duration,
                completed_days_ago=60 - duration,
            )
            for index, duration in enumerate((3, 4, 5, 6, 7))
        ]

        result = evaluate(build_facts(open_items, closed_items), context())

        self.assertIs(result.status, DataStatus.MEASURED)
        # 45 % * 100 (нет брошенных) + 30 % * 50 (решено 5 из 8) + 25 % * 100 (медиана 5 дней)
        self.assertAlmostEqual(result.score, 85.0)
        self.assertEqual(
            {metric.code for metric in result.metrics},
            {"stale_open_ratio", "backlog_trend", "median_days_to_close"},
        )

    def test_stale_issues_drive_the_score_down_and_raise_a_recommendation(self) -> None:
        open_items = [
            raw_issue(
                f"stale-{index}",
                created_days_ago=400,
                updated_days_ago=STALE_AFTER_DAYS + 110,
                status_type="initial",
            )
            for index in range(6)
        ] + [
            open_issue(f"fresh-{index}", created_days_ago=300, updated_days_ago=3)
            for index in range(4)
        ]

        result = evaluate(build_facts(open_items, []), context())

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 0.0)
        self.assertEqual(
            [recommendation.priority for recommendation in result.recommendations],
            [RecommendationPriority.P1],
        )
        self.assertTrue(result.recommendations[0].evidence)

    def test_evidence_links_to_the_issue_in_the_interface(self) -> None:
        open_items = [open_issue("65", created_days_ago=400, updated_days_ago=200)]

        result = evaluate(build_facts(open_items, []), context())

        evidence = result.metrics[0].evidence
        self.assertEqual(
            [item.url for item in evidence],
            ["https://sourcecraft.dev/team/platform/issues/65"],
        )

    def test_evidence_has_no_link_when_repository_url_is_unknown(self) -> None:
        anonymous = AnalysisContext(
            repository=RepositoryRef(id="r", organization_slug="team", repository_slug="platform"),
            commit_sha="0" * 40,
            analyzed_at=ANALYZED_AT,
            period_start=PERIOD_START,
            period_end=ANALYZED_AT,
        )
        open_items = [open_issue("65", created_days_ago=400, updated_days_ago=200)]

        result = evaluate(build_facts(open_items, []), anonymous)

        self.assertIsNone(result.metrics[0].evidence[0].url)

    def test_cancelled_issues_are_not_counted_as_resolved(self) -> None:
        created_recently = [
            raw_issue(f"cancelled-{index}", created_days_ago=30, updated_days_ago=10,
                      completed_days_ago=5, status_type="cancelled")
            for index in range(8)
        ]

        result = evaluate(build_facts([], created_recently), context())

        self.assertIs(result.status, DataStatus.MEASURED)
        backlog = next(metric for metric in result.metrics if metric.code == "backlog_trend")
        self.assertEqual(backlog.value, 0.0)

    def test_repository_without_issues_is_not_applicable(self) -> None:
        result = evaluate(build_facts([], []), context())

        self.assertIs(result.status, DataStatus.NOT_APPLICABLE)
        self.assertIsNone(result.score)
        self.assertIsNotNone(result.reason)

    def test_source_failure_is_unavailable_and_never_zero(self) -> None:
        result = evaluate(IssuesFacts(error="SourceCraft denied access with HTTP 403"), context())

        self.assertIs(result.status, DataStatus.UNAVAILABLE)
        self.assertIsNone(result.score)
        self.assertIn("403", result.reason or "")

    def test_truncated_list_is_insufficient_sample(self) -> None:
        open_items = [
            open_issue(f"open-{index}", created_days_ago=20, updated_days_ago=2)
            for index in range(5)
        ]

        result = evaluate(build_facts(open_items, [], open_truncated=True), context())

        self.assertIs(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)

    def test_same_facts_and_context_give_the_same_score(self) -> None:
        open_items = [
            raw_issue("open-1", created_days_ago=50, updated_days_ago=120, status_type="initial"),
            raw_issue("open-2", created_days_ago=50, updated_days_ago=4, status_type="initial"),
        ]
        facts = build_facts(open_items, [])

        first = evaluate(facts, context())
        second = evaluate(facts, context())

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.summary, second.summary)


if __name__ == "__main__":
    unittest.main()
