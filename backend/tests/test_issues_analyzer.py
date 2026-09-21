from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

import httpx

from backend.app.analyzers.issues import (
    STALE_AFTER_DAYS,
    IssuesFacts,
    build_facts,
    collect,
    evaluate,
)
from backend.app.contracts import AnalysisContext, DataStatus, RecommendationPriority, RepositoryRef
from backend.app.integrations.sourcecraft import SourceCraftClient

ANALYZED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
PERIOD_START = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)

REPOSITORY = RepositoryRef(
    id="repo-1",
    organization_slug="team",
    repository_slug="platform",
    web_url="https://sourcecraft.dev/team/platform",
)


def context(repository: RepositoryRef = REPOSITORY) -> AnalysisContext:
    return AnalysisContext(
        repository=repository,
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


def make_client(handler) -> SourceCraftClient:
    """Клиент SourceCraft на искусственном транспорте: тесты не выходят в сеть."""
    http_client = httpx.Client(
        base_url="https://api.sourcecraft.tech",
        transport=httpx.MockTransport(handler),
    )
    return SourceCraftClient("test-token", http_client=http_client)


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

    def test_aging_issues_raise_only_a_low_priority_recommendation(self) -> None:
        # Созданы до периода анализа, поэтому динамика разбора не измеряется,
        # а возраст обновления попадает между порогами AGING и STALE.
        open_items = [
            open_issue(f"aging-{index}", created_days_ago=300, updated_days_ago=45)
            for index in range(3)
        ]

        result = evaluate(build_facts(open_items, []), context())

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 100.0)
        self.assertEqual(
            [(item.code, item.priority) for item in result.recommendations],
            [("issues-aging-watch", RecommendationPriority.P3)],
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
        anonymous = context(
            RepositoryRef(id="r", organization_slug="team", repository_slug="platform")
        )
        open_items = [open_issue("65", created_days_ago=400, updated_days_ago=200)]

        result = evaluate(build_facts(open_items, []), anonymous)

        self.assertIsNone(result.metrics[0].evidence[0].url)

    def test_cancelled_issues_are_not_counted_as_resolved(self) -> None:
        cancelled = [
            raw_issue(
                f"cancelled-{index}",
                created_days_ago=30,
                updated_days_ago=10,
                completed_days_ago=5,
                status_type="cancelled",
            )
            for index in range(8)
        ]

        result = evaluate(build_facts([], cancelled), context())

        self.assertIs(result.status, DataStatus.MEASURED)
        backlog = next(metric for metric in result.metrics if metric.code == "backlog_trend")
        self.assertEqual(backlog.value, 0.0)
        self.assertIn("отменено 8", backlog.summary)

    def test_resolution_time_ignores_issues_closed_before_the_period(self) -> None:
        # Быстрые закрытия годичной давности не должны улучшать сегодняшнюю оценку.
        old = [
            raw_issue(
                f"old-{index}",
                created_days_ago=400,
                updated_days_ago=399,
                completed_days_ago=399,
            )
            for index in range(5)
        ]

        result = evaluate(build_facts([], old), context())

        self.assertNotIn(
            "median_days_to_close", {metric.code for metric in result.metrics}
        )

    def test_repository_without_issues_is_not_applicable(self) -> None:
        result = evaluate(build_facts([], []), context())

        self.assertIs(result.status, DataStatus.NOT_APPLICABLE)
        self.assertIsNone(result.score)
        self.assertIsNotNone(result.reason)

    def test_source_failure_is_unavailable_and_never_zero(self) -> None:
        facts = IssuesFacts(
            open_error="SourceCraft denied access with HTTP 403",
            closed_error="SourceCraft denied access with HTTP 403",
        )

        result = evaluate(facts, context())

        self.assertIs(result.status, DataStatus.UNAVAILABLE)
        self.assertIsNone(result.score)
        self.assertIn("403", result.reason or "")

    def test_partial_failure_keeps_the_metrics_that_still_have_data(self) -> None:
        open_items = [
            open_issue(f"stale-{index}", created_days_ago=400, updated_days_ago=200)
            for index in range(4)
        ]
        facts = build_facts(
            open_items, [], closed_error="SourceCraft returned unexpected HTTP 500"
        )

        result = evaluate(facts, context())

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertEqual({metric.code for metric in result.metrics}, {"stale_open_ratio"})
        self.assertIn("часть данных недоступна", result.summary)

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
            open_issue("open-1", created_days_ago=50, updated_days_ago=120),
            open_issue("open-2", created_days_ago=50, updated_days_ago=4),
        ]
        facts = build_facts(open_items, [])

        first = evaluate(facts, context())
        second = evaluate(facts, context())

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.summary, second.summary)


class IssuesParsingTest(unittest.TestCase):
    """Кривой ответ API не должен ронять расчёт."""

    def test_timestamps_without_timezone_are_treated_as_utc(self) -> None:
        naive = {
            "slug": "1",
            "title": "Без часового пояса",
            "created_at": "2026-05-01T10:00:00",
            "updated_at": "2026-05-01T10:00:00",
            "status": {"status_type": "initial"},
        }

        result = evaluate(build_facts([naive], []), context())

        self.assertIs(result.status, DataStatus.MEASURED)

    def test_non_object_status_does_not_crash_the_analyzer(self) -> None:
        broken = {
            "slug": "1",
            "title": "Статус строкой",
            "created_at": moment(10),
            "updated_at": moment(10),
            "status": "open",
        }

        facts = build_facts([broken], [])

        self.assertEqual(facts.open_issues[0].status_type, "")
        self.assertIs(evaluate(facts, context()).status, DataStatus.MEASURED)

    def test_issues_without_dates_are_not_treated_as_unused_tracker(self) -> None:
        facts = build_facts([{"slug": "1", "title": "Без даты"}, "мусор"], [])

        result = evaluate(facts, context())

        self.assertEqual(facts.skipped_count, 2)
        self.assertIs(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        self.assertIsNotNone(result.reason)

    def test_closed_without_completed_at_still_counts_as_resolved(self) -> None:
        closed = [
            raw_issue(
                f"closed-{index}",
                created_days_ago=40,
                updated_days_ago=10,
                completed_days_ago=None,
                status_type="completed",
            )
            for index in range(4)
        ]

        result = evaluate(build_facts([], closed), context())

        self.assertIs(result.status, DataStatus.MEASURED)
        backlog = next(metric for metric in result.metrics if metric.code == "backlog_trend")
        self.assertEqual(backlog.value, 1.0)
        self.assertIn("updated_at", backlog.summary)
        self.assertIn(
            "median_days_to_close", {metric.code for metric in result.metrics}
        )

    def test_backlog_score_does_not_exceed_100_when_more_closed_than_opened(self) -> None:
        created = [
            raw_issue(
                "new-1",
                created_days_ago=20,
                updated_days_ago=5,
                completed_days_ago=5,
            )
        ]
        older = [
            raw_issue(
                f"old-{index}",
                created_days_ago=400,
                updated_days_ago=10,
                completed_days_ago=10,
            )
            for index in range(5)
        ]

        result = evaluate(build_facts([], created + older), context())

        backlog = next(metric for metric in result.metrics if metric.code == "backlog_trend")
        self.assertGreater(backlog.value, 1)
        self.assertEqual(backlog.normalized_score, 100.0)


class IssuesCollectTest(unittest.TestCase):
    """Проверяет обход страниц и обработку ошибок без обращения к сети."""

    def test_collect_follows_pagination_to_the_last_page(self) -> None:
        seen: list[tuple[str | None, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            status_filter = params.get("filter")
            token = params.get("page_token")
            seen.append((status_filter, token))

            if status_filter == "status=in_progress":
                return httpx.Response(200, json={"issues": [], "next_page_token": ""})
            if status_filter == "status=open" and token is None:
                return httpx.Response(
                    200,
                    json={
                        "issues": [open_issue("1", created_days_ago=10, updated_days_ago=2)],
                        "next_page_token": "page-2",
                    },
                )
            if status_filter == "status=open":
                return httpx.Response(
                    200,
                    json={
                        "issues": [open_issue("2", created_days_ago=10, updated_days_ago=2)],
                        "next_page_token": "",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "issues": [
                        raw_issue("3", created_days_ago=20, updated_days_ago=15,
                                  completed_days_ago=15)
                    ],
                    "next_page_token": "",
                },
            )

        facts = collect(make_client(handler), REPOSITORY)

        self.assertEqual(len(facts.open_issues), 2)
        self.assertEqual(len(facts.closed_issues), 1)
        self.assertFalse(facts.open_truncated)
        self.assertFalse(facts.closed_truncated)
        self.assertEqual(seen[0], ("status=open", None))
        self.assertEqual(seen[1], ("status=open", "page-2"))
        self.assertIn(("status=in_progress", None), seen)

    def test_collect_includes_in_progress_issues_with_open_ones(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            status_filter = request.url.params.get("filter")
            if status_filter == "status=open":
                issues = [open_issue("1", created_days_ago=10, updated_days_ago=2)]
            elif status_filter == "status=in_progress":
                issues = [
                    raw_issue(
                        "2",
                        created_days_ago=10,
                        updated_days_ago=1,
                        status_type="in_progress",
                    )
                ]
            else:
                issues = []
            return httpx.Response(200, json={"issues": issues, "next_page_token": ""})

        facts = collect(make_client(handler), REPOSITORY)

        self.assertEqual({issue.slug for issue in facts.open_issues}, {"1", "2"})
        self.assertEqual(facts.closed_issues, ())

    def test_collect_marks_truncation_when_the_page_budget_runs_out(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "issues": [open_issue("1", created_days_ago=10, updated_days_ago=2)],
                    "next_page_token": "more",
                },
            )

        facts = collect(make_client(handler), REPOSITORY, max_pages=2)

        self.assertTrue(facts.open_truncated)
        self.assertTrue(facts.closed_truncated)
        self.assertGreaterEqual(len(facts.open_issues), 1)

    def test_collect_turns_authentication_failure_into_a_fact(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        facts = collect(make_client(handler), REPOSITORY)

        self.assertIsNotNone(facts.open_error)
        self.assertIsNotNone(facts.closed_error)
        self.assertIs(evaluate(facts, context()).status, DataStatus.UNAVAILABLE)

    def test_collect_rejects_a_payload_that_is_not_an_object(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        facts = collect(make_client(handler), REPOSITORY)

        self.assertIn("object", facts.open_error or "")

    def test_collect_keeps_the_open_list_when_the_closed_list_fails(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("filter") == "status=closed":
                return httpx.Response(500)
            return httpx.Response(
                200,
                json={
                    "issues": [open_issue("1", created_days_ago=400, updated_days_ago=200)],
                    "next_page_token": "",
                },
            )

        facts = collect(make_client(handler), REPOSITORY)

        self.assertIsNone(facts.open_error)
        self.assertIsNotNone(facts.closed_error)
        self.assertEqual(len(facts.open_issues), 1)
        self.assertIs(evaluate(facts, context()).status, DataStatus.MEASURED)

    def test_collect_keeps_partial_open_error_when_in_progress_fails(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            status_filter = request.url.params.get("filter")
            if status_filter == "status=in_progress":
                return httpx.Response(500)
            if status_filter == "status=closed":
                return httpx.Response(
                    200,
                    json={
                        "issues": [
                            raw_issue(
                                f"closed-{index}",
                                created_days_ago=20,
                                updated_days_ago=5,
                                completed_days_ago=5,
                            )
                            for index in range(3)
                        ],
                        "next_page_token": "",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "issues": [open_issue("1", created_days_ago=400, updated_days_ago=200)],
                    "next_page_token": "",
                },
            )

        facts = collect(make_client(handler), REPOSITORY)

        self.assertIsNotNone(facts.open_error)
        self.assertIn("in_progress", facts.open_error or "")
        self.assertEqual(len(facts.open_issues), 1)
        self.assertIsNone(facts.closed_error)
        self.assertEqual(len(facts.closed_issues), 3)

        result = evaluate(facts, context())

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertEqual({metric.code for metric in result.metrics}, {"median_days_to_close"})
        self.assertIn("часть данных недоступна", result.summary)


if __name__ == "__main__":
    unittest.main()
