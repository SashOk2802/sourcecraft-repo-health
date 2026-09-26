from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

import httpx

from backend.app.analyzers.activity import (
    ACTIVE_WEEKS_CAP,
    MERGED_MR_CAP,
    PAGE_SIZE,
    RECENCY_BEST_DAYS,
    RECENCY_STALE_DAYS,
    RECENCY_WORST_DAYS,
    RELEASE_CAP,
    ActivityFacts,
    CommitHistoryFacts,
    build_facts,
    collect,
    collect_commit_history,
    evaluate,
)
from backend.app.contracts import AnalysisContext, DataStatus, RecommendationPriority, RepositoryRef
from backend.app.integrations.sourcecraft import SourceCraftClient

ANALYZED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
PERIOD_START = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
REPO_PATH = "/repos/team/platform"

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


def raw_pull(
    slug: str,
    *,
    created_days_ago: int,
    updated_days_ago: int,
    status: str = "merged",
) -> dict[str, object]:
    return {
        "slug": slug,
        "title": f"MR {slug}",
        "status": status,
        "created_at": moment(created_days_ago),
        "updated_at": moment(updated_days_ago),
    }


def raw_release(
    tag: str,
    *,
    days_ago: int,
    status: str = "published",
    is_pre_release: bool = False,
) -> dict[str, object]:
    return {
        "tag": tag,
        "title": tag,
        "status": status,
        "is_pre_release": is_pre_release,
        "released_at": moment(days_ago),
        "created_at": moment(days_ago),
    }


def raw_contributor(username: str) -> dict[str, object]:
    return {"id": username, "username": username, "display_name": username}


def make_client(handler) -> SourceCraftClient:
    http_client = httpx.Client(
        base_url="https://api.sourcecraft.tech",
        transport=httpx.MockTransport(handler),
    )
    return SourceCraftClient("test-token", http_client=http_client)


class ActivityEvaluateTest(unittest.TestCase):
    """Проверяет методику Activity на подготовленных фактах, без сети."""

    def test_healthy_project_uses_all_metrics_and_caps_volume(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=3),
            contributor_items=[raw_contributor("a"), raw_contributor("b"), raw_contributor("c")],
            pull_items=[
                raw_pull(str(index), created_days_ago=20, updated_days_ago=10)
                for index in range(20)
            ],
            release_items=[
                raw_release(f"v1.{index}", days_ago=8) for index in range(10)
            ],
        )

        result = evaluate(facts, context())

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 100.0)
        self.assertEqual(
            {metric.code for metric in result.metrics},
            {
                "last_activity_days",
                "merged_mr_in_period",
                "releases_in_period",
                "contributor_count",
            },
        )
        merged = next(metric for metric in result.metrics if metric.code == "merged_mr_in_period")
        releases = next(metric for metric in result.metrics if metric.code == "releases_in_period")
        self.assertEqual(merged.value, 20)
        self.assertEqual(merged.normalized_score, 100.0)
        self.assertEqual(releases.value, 10)
        self.assertIn(f"первые {MERGED_MR_CAP}", merged.summary)

    def test_three_merges_score_the_same_as_twenty(self) -> None:
        few = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=3),
            contributor_items=[
                raw_contributor("a"),
                raw_contributor("b"),
                raw_contributor("c"),
            ],
            pull_items=[
                raw_pull(str(index), created_days_ago=20, updated_days_ago=10)
                for index in range(MERGED_MR_CAP)
            ],
            release_items=[
                raw_release("v1.0", days_ago=8),
                raw_release("v1.1", days_ago=7),
                raw_release("v1.2", days_ago=6),
            ],
        )
        many = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=3),
            contributor_items=[
                raw_contributor("a"),
                raw_contributor("b"),
                raw_contributor("c"),
            ],
            pull_items=[
                raw_pull(str(index), created_days_ago=20, updated_days_ago=10)
                for index in range(50)
            ],
            release_items=[raw_release(f"v{index}", days_ago=8) for index in range(20)],
        )

        self.assertEqual(evaluate(few, context()).score, evaluate(many, context()).score)

    def test_contributor_score_caps_at_three_people(self) -> None:
        def metric_for(*names: str) -> float:
            facts = build_facts(
                last_updated=ANALYZED_AT - timedelta(days=1),
                contributor_items=[raw_contributor(name) for name in names],
            )
            result = evaluate(facts, context())
            contributors = next(
                item for item in result.metrics if item.code == "contributor_count"
            )
            return contributors.normalized_score

        one = metric_for("a")
        three = metric_for("a", "b", "c")
        ten = metric_for(*[str(index) for index in range(10)])

        self.assertLess(one, three)
        self.assertEqual(three, 100.0)
        self.assertEqual(ten, three)

    def test_stale_repository_scores_low_and_raises_p1(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=int(RECENCY_WORST_DAYS) + 40),
            contributor_items=[raw_contributor("only")],
        )

        result = evaluate(facts, context())

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertLess(result.score, 30)
        recency = next(metric for metric in result.metrics if metric.code == "last_activity_days")
        self.assertGreater(recency.value, RECENCY_STALE_DAYS)
        self.assertEqual(recency.normalized_score, 0.0)
        self.assertEqual(
            [item.code for item in result.recommendations],
            ["activity-stale-repository"],
        )
        self.assertEqual(result.recommendations[0].priority, RecommendationPriority.P1)
        self.assertGreater(result.recommendations[0].expected_score_delta or 0, 0)

    def test_recency_recommendation_starts_after_ninety_days(self) -> None:
        at_threshold = evaluate(
            build_facts(
                last_updated=ANALYZED_AT - timedelta(days=RECENCY_STALE_DAYS),
                contributor_items=[raw_contributor("a")],
            ),
            context(),
        )
        just_over = evaluate(
            build_facts(
                last_updated=ANALYZED_AT - timedelta(days=RECENCY_STALE_DAYS + 1),
                contributor_items=[raw_contributor("a")],
            ),
            context(),
        )

        self.assertEqual(at_threshold.recommendations, ())
        self.assertEqual(
            [item.code for item in just_over.recommendations],
            ["activity-stale-repository"],
        )

    def test_recency_score_is_linear_inside_the_window(self) -> None:
        midpoint_days = int((RECENCY_BEST_DAYS + RECENCY_WORST_DAYS) / 2)
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=midpoint_days),
            contributor_items=[raw_contributor("a")],
        )

        recency = next(
            metric
            for metric in evaluate(facts, context()).metrics
            if metric.code == "last_activity_days"
        )
        self.assertEqual(recency.value, midpoint_days)
        self.assertAlmostEqual(recency.normalized_score, 50.0, places=5)

    def test_merges_and_releases_before_the_period_are_ignored(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            contributor_items=[raw_contributor("a")],
            pull_items=[
                raw_pull("old", created_days_ago=250, updated_days_ago=200),
                raw_pull("future", created_days_ago=10, updated_days_ago=-5),
            ],
            release_items=[raw_release("v0.1", days_ago=200)],
        )

        result = evaluate(facts, context())
        merged = next(metric for metric in result.metrics if metric.code == "merged_mr_in_period")
        releases = next(metric for metric in result.metrics if metric.code == "releases_in_period")

        self.assertEqual(merged.value, 0)
        self.assertEqual(releases.value, 0)
        self.assertIn("activity-mr-not-merged", [item.code for item in result.recommendations])

    def test_merge_requests_without_merge_are_not_counted_as_delivery(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            contributor_items=[raw_contributor("a")],
            pull_items=[
                raw_pull("1", created_days_ago=10, updated_days_ago=1, status="open"),
                raw_pull("2", created_days_ago=40, updated_days_ago=20, status="discarded"),
            ],
        )

        result = evaluate(facts, context())

        merged = next(metric for metric in result.metrics if metric.code == "merged_mr_in_period")
        self.assertEqual(merged.value, 0)
        self.assertEqual(merged.normalized_score, 0.0)
        self.assertIn("activity-mr-not-merged", [item.code for item in result.recommendations])

    def test_old_release_raises_p3_when_nothing_worse_is_found(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            contributor_items=[raw_contributor("a")],
            release_items=[raw_release("v1.0", days_ago=200)],
        )

        result = evaluate(facts, context())

        self.assertEqual(
            [(item.code, item.priority) for item in result.recommendations],
            [("activity-no-recent-release", RecommendationPriority.P3)],
        )
        evidence = result.recommendations[0].evidence
        self.assertEqual(
            [item.url for item in evidence],
            ["https://sourcecraft.dev/team/platform/releases/v1.0"],
        )
        self.assertGreater(result.recommendations[0].expected_score_delta or 0, 0)

    def test_p3_release_watch_is_suppressed_when_p1_exists(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=int(RECENCY_WORST_DAYS) + 10),
            contributor_items=[raw_contributor("a")],
            release_items=[raw_release("v1.0", days_ago=200)],
        )

        result = evaluate(facts, context())

        self.assertEqual(
            [item.code for item in result.recommendations],
            ["activity-stale-repository"],
        )

    def test_draft_release_is_not_a_published_release(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            contributor_items=[raw_contributor("a")],
            release_items=[
                raw_release("v1.0", days_ago=200, status="published"),
                raw_release("v2.0-rc", days_ago=3, status="draft"),
            ],
        )

        releases = next(
            metric
            for metric in evaluate(facts, context()).metrics
            if metric.code == "releases_in_period"
        )
        self.assertEqual(releases.value, 0)

    def test_published_prerelease_still_counts(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            contributor_items=[raw_contributor("a")],
            release_items=[raw_release("v2.0-rc", days_ago=3, is_pre_release=True)],
        )

        releases = next(
            metric
            for metric in evaluate(facts, context()).metrics
            if metric.code == "releases_in_period"
        )
        self.assertEqual(releases.value, 1)
        self.assertAlmostEqual(releases.normalized_score, 100.0 / RELEASE_CAP)

    def test_repository_without_mr_workflow_does_not_zero_the_category(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            contributor_items=[raw_contributor("a")],
        )

        result = evaluate(facts, context())

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertNotIn("merged_mr_in_period", {metric.code for metric in result.metrics})
        self.assertNotIn("releases_in_period", {metric.code for metric in result.metrics})
        recency = next(metric for metric in result.metrics if metric.code == "last_activity_days")
        self.assertEqual(recency.normalized_score, 100.0)
        self.assertGreater(result.score, 70)

    def test_no_releases_is_not_applicable_for_that_metric(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            contributor_items=[raw_contributor("a")],
            pull_items=[raw_pull("1", created_days_ago=10, updated_days_ago=2)],
        )

        result = evaluate(facts, context())

        self.assertNotIn("releases_in_period", {metric.code for metric in result.metrics})

    def test_empty_repository_is_not_applicable(self) -> None:
        result = evaluate(build_facts(is_empty=True), context())

        self.assertIs(result.status, DataStatus.NOT_APPLICABLE)
        self.assertIsNone(result.score)

    def test_empty_facts_are_insufficient_sample_not_zero(self) -> None:
        result = evaluate(build_facts(), context())

        self.assertIs(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        self.assertEqual(result.metrics, ())

    def test_truncated_lists_without_recency_are_insufficient_sample(self) -> None:
        result = evaluate(
            build_facts(pulls_truncated=True, releases_truncated=True, contributors_truncated=True),
            context(),
        )

        self.assertIs(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)

    def test_all_sources_failing_is_unavailable(self) -> None:
        facts = ActivityFacts(
            repository_error="HTTP 403",
            contributors_error="HTTP 403",
            pulls_error="HTTP 403",
            releases_error="HTTP 403",
        )

        result = evaluate(facts, context())

        self.assertIs(result.status, DataStatus.UNAVAILABLE)
        self.assertIsNone(result.score)

    def test_partial_failure_keeps_metrics_that_still_have_data(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            contributor_items=[raw_contributor("a")],
            pulls_error="HTTP 500",
            releases_error="HTTP 500",
        )

        result = evaluate(facts, context())

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertEqual(
            {metric.code for metric in result.metrics},
            {"last_activity_days", "contributor_count"},
        )
        self.assertIn("часть данных недоступна", result.summary)

    def test_truncated_pulls_drop_only_the_mr_metric(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            contributor_items=[raw_contributor("a")],
            pull_items=[raw_pull("1", created_days_ago=10, updated_days_ago=2)],
            pulls_truncated=True,
        )

        result = evaluate(facts, context())

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertEqual(
            {metric.code for metric in result.metrics},
            {"last_activity_days", "contributor_count"},
        )

    def test_skipped_pull_does_not_drop_contributors(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            contributor_items=[raw_contributor("a")],
            pull_items=[{"slug": "broken"}],
        )

        result = evaluate(facts, context())

        self.assertEqual(facts.skipped_count, 1)
        self.assertIn("contributor_count", {metric.code for metric in result.metrics})
        self.assertNotIn("merged_mr_in_period", {metric.code for metric in result.metrics})

    def test_evidence_links_to_merge_request(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            pull_items=[raw_pull("78", created_days_ago=10, updated_days_ago=2)],
        )

        result = evaluate(facts, context())
        merged = next(metric for metric in result.metrics if metric.code == "merged_mr_in_period")

        self.assertEqual(
            [item.url for item in merged.evidence],
            ["https://sourcecraft.dev/team/platform/pr/78"],
        )

    def test_evidence_has_no_link_when_repository_url_is_unknown(self) -> None:
        anonymous = context(
            RepositoryRef(id="r", organization_slug="team", repository_slug="platform")
        )
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            pull_items=[raw_pull("78", created_days_ago=10, updated_days_ago=2)],
        )

        merged = next(
            metric
            for metric in evaluate(facts, anonymous).metrics
            if metric.code == "merged_mr_in_period"
        )
        self.assertIsNone(merged.evidence[0].url)

    def test_same_facts_give_the_same_score(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=10),
            contributor_items=[raw_contributor("a")],
            pull_items=[raw_pull("1", created_days_ago=12, updated_days_ago=10)],
        )

        first = evaluate(facts, context())
        second = evaluate(facts, context())

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.summary, second.summary)


class ActivityParsingTest(unittest.TestCase):
    """Кривой ответ API не должен ронять расчёт."""

    def test_timestamps_without_timezone_are_treated_as_utc(self) -> None:
        naive = {
            "slug": "1",
            "title": "Без пояса",
            "status": "merged",
            "created_at": "2026-05-01T10:00:00",
            "updated_at": "2026-05-01T10:00:00",
        }

        result = evaluate(build_facts(pull_items=[naive]), context())

        self.assertIs(result.status, DataStatus.MEASURED)

    def test_z_suffix_and_id_fallback_are_accepted(self) -> None:
        pull = {
            "id": "78",
            "title": "MR",
            "status": "merged",
            "created_at": "2026-09-01T10:00:00Z",
            "updated_at": "2026-09-01T10:00:00Z",
        }
        release = {
            "id": "rel-1",
            "status": "published",
            "created_at": "2026-09-01T10:00:00Z",
        }

        facts = build_facts(pull_items=[pull], release_items=[release])

        self.assertEqual(facts.pulls[0].slug, "78")
        self.assertEqual(facts.releases[0].tag, "rel-1")
        self.assertIs(evaluate(facts, context()).status, DataStatus.MEASURED)

    def test_records_without_dates_are_skipped(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            contributor_items=[raw_contributor("a")],
            pull_items=[{"slug": "1", "title": "Без даты"}, "мусор"],
        )

        result = evaluate(facts, context())

        self.assertEqual(facts.skipped_count, 2)
        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertNotIn("merged_mr_in_period", {metric.code for metric in result.metrics})


class ActivityCollectTest(unittest.TestCase):
    """Обход страниц и ошибки источников без сети."""

    def test_collect_reads_repository_and_paginated_lists(self) -> None:
        seen: list[tuple[str, str | None, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            token = request.url.params.get("page_token")
            seen.append((path, token, request.url.params.get("page_size")))
            if path.endswith("/contributors"):
                return httpx.Response(
                    200,
                    json={"contributors": [raw_contributor("a")], "next_page_token": ""},
                )
            if path == REPO_PATH:
                return httpx.Response(
                    200,
                    json={"last_updated": moment(4), "is_empty": False},
                )
            if path.endswith("/pulls") and token is None:
                return httpx.Response(
                    200,
                    json={
                        "pull_requests": [raw_pull("1", created_days_ago=10, updated_days_ago=2)],
                        "next_page_token": "page-2",
                    },
                )
            if path.endswith("/pulls"):
                return httpx.Response(
                    200,
                    json={
                        "pull_requests": [raw_pull("2", created_days_ago=8, updated_days_ago=1)],
                        "next_page_token": "",
                    },
                )
            if path.endswith("/releases"):
                return httpx.Response(
                    200,
                    json={
                        "releases": [raw_release("v1.0", days_ago=3)],
                        "next_page_token": "",
                    },
                )
            return httpx.Response(404)

        facts = collect(make_client(handler), REPOSITORY)

        self.assertEqual(facts.last_updated, ANALYZED_AT - timedelta(days=4))
        self.assertEqual(len(facts.pulls), 2)
        self.assertEqual(len(facts.releases), 1)
        self.assertEqual(len(facts.contributors), 1)
        self.assertFalse(facts.pulls_truncated)
        self.assertIn((f"{REPO_PATH}/pulls", None, str(PAGE_SIZE)), seen)
        self.assertIn((f"{REPO_PATH}/pulls", "page-2", str(PAGE_SIZE)), seen)

    def test_collect_marks_truncation_when_page_budget_runs_out(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == REPO_PATH:
                return httpx.Response(200, json={"last_updated": moment(1), "is_empty": False})
            return httpx.Response(
                200,
                json={
                    "pull_requests": [],
                    "contributors": [],
                    "releases": [],
                    "next_page_token": "more",
                },
            )

        facts = collect(make_client(handler), REPOSITORY, max_pages=1)

        self.assertTrue(facts.pulls_truncated)
        self.assertTrue(facts.releases_truncated)
        self.assertTrue(facts.contributors_truncated)

    def test_collect_turns_403_into_facts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        facts = collect(make_client(handler), REPOSITORY)

        self.assertIsNotNone(facts.repository_error)
        self.assertIsNotNone(facts.pulls_error)
        self.assertIs(evaluate(facts, context()).status, DataStatus.UNAVAILABLE)

    def test_collect_rejects_a_payload_that_is_not_an_object(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        facts = collect(make_client(handler), REPOSITORY)

        self.assertIn("object", facts.repository_error or "")
        self.assertIn("object", facts.pulls_error or "")

    def test_collect_encodes_org_and_repo_in_the_path(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"last_updated": moment(1), "is_empty": False})

        repository = RepositoryRef(
            id="r",
            organization_slug="org name",
            repository_slug="repo/name",
        )
        collect(make_client(handler), repository)

        self.assertTrue(any("org%20name" in path and "repo%2Fname" in path for path in seen))

    def test_active_weeks_cap_and_same_week_commits(self) -> None:
        same_week = CommitHistoryFacts(
            collected=True,
            committed_at=(
                ANALYZED_AT - timedelta(days=10),
                ANALYZED_AT - timedelta(days=9),
            ),
        )
        many_weeks = CommitHistoryFacts(
            collected=True,
            committed_at=tuple(
                ANALYZED_AT - timedelta(days=7 * index) for index in range(1, ACTIVE_WEEKS_CAP + 3)
            ),
        )
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            contributor_items=[raw_contributor("a")],
            commit_history=same_week,
        )
        crowded = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            contributor_items=[raw_contributor("a")],
            commit_history=many_weeks,
        )

        one_week = next(
            metric
            for metric in evaluate(facts, context()).metrics
            if metric.code == "active_weeks_in_period"
        )
        capped = next(
            metric
            for metric in evaluate(crowded, context()).metrics
            if metric.code == "active_weeks_in_period"
        )

        self.assertEqual(one_week.value, 1)
        self.assertAlmostEqual(one_week.normalized_score, 100.0 / ACTIVE_WEEKS_CAP)
        self.assertGreater(capped.value, ACTIVE_WEEKS_CAP)
        self.assertEqual(capped.normalized_score, 100.0)

    def test_missing_or_broken_history_does_not_change_api_score(self) -> None:
        api_facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            contributor_items=[raw_contributor("a")],
            pull_items=[raw_pull("1", created_days_ago=4, updated_days_ago=2)],
        )
        broken = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            contributor_items=[raw_contributor("a")],
            pull_items=[raw_pull("1", created_days_ago=4, updated_days_ago=2)],
            commit_history=CommitHistoryFacts(collected=True, error="Не удалось прочитать историю коммитов."),
        )
        truncated = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            contributor_items=[raw_contributor("a")],
            pull_items=[raw_pull("1", created_days_ago=4, updated_days_ago=2)],
            commit_history=CommitHistoryFacts(
                collected=True,
                committed_at=(ANALYZED_AT - timedelta(days=2),),
                truncated=True,
            ),
        )

        baseline = evaluate(api_facts, context())
        without_history = evaluate(broken, context())
        partial_history = evaluate(truncated, context())

        self.assertEqual(baseline.score, without_history.score)
        self.assertEqual(baseline.score, partial_history.score)
        self.assertNotIn(
            "active_weeks_in_period",
            {metric.code for metric in without_history.metrics},
        )
        self.assertIn("история коммитов недоступна", without_history.summary)
        self.assertIn("история коммитов прочитана не полностью", partial_history.summary)

    def test_zero_commits_raise_p2_until_repository_is_already_stale(self) -> None:
        quiet = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=2),
            contributor_items=[raw_contributor("a")],
            commit_history=CommitHistoryFacts(collected=True, committed_at=()),
        )
        stale = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=int(RECENCY_WORST_DAYS) + 5),
            contributor_items=[raw_contributor("a")],
            commit_history=CommitHistoryFacts(collected=True, committed_at=()),
        )

        quiet_result = evaluate(quiet, context())
        stale_result = evaluate(stale, context())
        weeks = next(
            metric
            for metric in quiet_result.metrics
            if metric.code == "active_weeks_in_period"
        )

        self.assertEqual(weeks.value, 0)
        self.assertEqual(weeks.normalized_score, 0.0)
        self.assertIn(
            "activity-no-commits-in-period",
            [item.code for item in quiet_result.recommendations],
        )
        self.assertEqual(
            [item.code for item in stale_result.recommendations],
            ["activity-stale-repository"],
        )

    def test_stale_repository_delta_reaches_best_recency(self) -> None:
        facts = build_facts(last_updated=ANALYZED_AT - timedelta(days=400))

        result = evaluate(facts, context())

        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.recommendations[0].expected_score_delta, 100.0)

    def test_collect_rejects_a_repeated_page_token(self) -> None:
        calls = {"pulls": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == REPO_PATH:
                return httpx.Response(200, json={"last_updated": moment(1), "is_empty": False})
            if request.url.path.endswith("/pulls"):
                calls["pulls"] += 1
            return httpx.Response(
                200,
                json={
                    "pull_requests": [raw_pull("1", created_days_ago=3, updated_days_ago=1)],
                    "contributors": [raw_contributor("a")],
                    "releases": [],
                    "next_page_token": "same",
                },
            )

        facts = collect(make_client(handler), REPOSITORY)

        self.assertIn("repeated", facts.pulls_error or "")
        self.assertEqual(facts.pulls, ())
        self.assertEqual(calls["pulls"], 2)
        self.assertIs(evaluate(facts, context()).status, DataStatus.MEASURED)

    def test_bad_clone_url_stays_inside_commit_history(self) -> None:
        repository = RepositoryRef(
            id="r",
            organization_slug="team",
            repository_slug="platform",
            web_url="http://evil.example/team/platform",
        )

        history = collect_commit_history(make_client(lambda request: httpx.Response(500)), context(repository))

        self.assertTrue(history.collected)
        self.assertIsNotNone(history.error)
        self.assertNotIn("test-token", history.error or "")

    def test_collect_keeps_repository_when_pulls_fail(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/pulls"):
                return httpx.Response(500)
            if path.endswith("/contributors"):
                return httpx.Response(200, json={"contributors": [], "next_page_token": ""})
            if path.endswith("/releases"):
                return httpx.Response(200, json={"releases": [], "next_page_token": ""})
            if path == REPO_PATH:
                return httpx.Response(200, json={"last_updated": moment(2), "is_empty": False})
            return httpx.Response(404)

        facts = collect(make_client(handler), REPOSITORY)

        self.assertIsNone(facts.repository_error)
        self.assertIsNotNone(facts.pulls_error)
        self.assertIs(evaluate(facts, context()).status, DataStatus.MEASURED)


if __name__ == "__main__":
    unittest.main()
