"""Activity v2: регулярные недели коммитов и production-подключение."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

from backend.app.analysis.providers import sourcecraft_analyzer_provider
from backend.app.analyzers.activity import (
    ACTIVE_WEEKS_CAP,
    COMMIT_HISTORY_LIMIT,
    COMMIT_HISTORY_TIMEOUT_SECONDS,
    CommitHistoryFacts,
    build_facts,
    collect_commit_history,
    evaluate,
)
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, InsightResult, RepositoryRef
from backend.app.integrations.git_repository import CommitRecord, CommitTimestampPage, GitCloneError

ANALYZED_AT = datetime(2026, 9, 15, 12, tzinfo=UTC)
CONTEXT = AnalysisContext(
    repository=RepositoryRef(
        id="repo-1",
        organization_slug="team",
        repository_slug="platform",
        web_url="https://sourcecraft.dev/team/platform",
    ),
    commit_sha="a" * 40,
    analyzed_at=ANALYZED_AT,
    period_start=datetime(2026, 3, 15, 12, tzinfo=UTC),
    period_end=ANALYZED_AT,
)


class ActivityWeeksV2Test(unittest.TestCase):
    def test_active_weeks_use_distinct_utc_weeks_and_cap_at_eight(self) -> None:
        moments = tuple(
            ANALYZED_AT - timedelta(weeks=index)
            for index in range(ACTIVE_WEEKS_CAP + 3)
        )
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            commit_history=CommitHistoryFacts(collected=True, committed_at=moments),
        )

        result = evaluate(facts, CONTEXT)

        metric = next(item for item in result.metrics if item.code == "active_weeks_in_period")
        self.assertEqual(metric.value, ACTIVE_WEEKS_CAP + 3)
        self.assertEqual(metric.normalized_score, 100.0)
        self.assertEqual(result.status, DataStatus.MEASURED)

    def test_empty_complete_history_is_a_measured_zero_and_recommendation(self) -> None:
        facts = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            commit_history=CommitHistoryFacts(collected=True),
        )

        result = evaluate(facts, CONTEXT)

        metric = next(item for item in result.metrics if item.code == "active_weeks_in_period")
        self.assertEqual(metric.value, 0)
        self.assertEqual(metric.normalized_score, 0.0)
        self.assertIn(
            "activity-no-commits-in-period",
            {item.code for item in result.recommendations},
        )

    def test_unavailable_or_truncated_history_does_not_change_other_activity_metrics(self) -> None:
        base = build_facts(last_updated=ANALYZED_AT - timedelta(days=1))
        unavailable = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            commit_history=CommitHistoryFacts(
                collected=True,
                error="commit_history_unavailable",
            ),
        )
        truncated = build_facts(
            last_updated=ANALYZED_AT - timedelta(days=1),
            commit_history=CommitHistoryFacts(collected=True, truncated=True),
        )

        expected = evaluate(base, CONTEXT)
        for facts in (unavailable, truncated):
            with self.subTest(history=facts.commit_history):
                result = evaluate(facts, CONTEXT)
                self.assertEqual(result.score, expected.score)
                self.assertNotIn(
                    "active_weeks_in_period",
                    {item.code for item in result.metrics},
                )

    def test_history_can_measure_activity_when_rest_api_is_unavailable(self) -> None:
        facts = build_facts(
            repository_error="HTTP 503",
            contributors_error="HTTP 503",
            pulls_error="HTTP 503",
            releases_error="HTTP 503",
            commit_history=CommitHistoryFacts(
                collected=True,
                committed_at=(ANALYZED_AT - timedelta(days=7),),
            ),
        )

        result = evaluate(facts, CONTEXT)

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(
            {item.code for item in result.metrics},
            {"active_weeks_in_period"},
        )


class ActivityHistoryCollectionTest(unittest.TestCase):
    @patch("backend.app.analyzers.activity.read_commit_timestamps")
    def test_history_uses_context_sha_and_safe_clone_url(self, reader: Mock) -> None:
        reader.return_value = CommitTimestampPage(
            committed_at=(ANALYZED_AT - timedelta(days=1),),
            truncated=False,
            commits=(
                CommitRecord(
                    committed_at=ANALYZED_AT - timedelta(days=1),
                    author_email="dev@example.com",
                    parent_count=1,
                ),
            ),
        )

        facts = collect_commit_history(CONTEXT, auth_token="token")

        self.assertEqual(
            facts,
            CommitHistoryFacts(
                collected=True,
                committed_at=(ANALYZED_AT - timedelta(days=1),),
                commits=(
                    CommitRecord(
                        committed_at=ANALYZED_AT - timedelta(days=1),
                        author_email="dev@example.com",
                        parent_count=1,
                    ),
                ),
            ),
        )
        reader.assert_called_once_with(
            "https://git@git.sourcecraft.dev/team/platform.git",
            since=CONTEXT.period_start,
            until=CONTEXT.period_end,
            revision=CONTEXT.commit_sha,
            auth_token="token",
            max_commits=COMMIT_HISTORY_LIMIT,
            timeout_seconds=COMMIT_HISTORY_TIMEOUT_SECONDS,
        )

    @patch(
        "backend.app.analyzers.activity.read_commit_timestamps",
        side_effect=GitCloneError("safe git failure"),
    )
    def test_history_failure_becomes_unavailable_metric(self, _: Mock) -> None:
        facts = collect_commit_history(CONTEXT, auth_token="token")

        self.assertEqual(
            facts,
            CommitHistoryFacts(collected=True, error="commit_history_unavailable"),
        )


class ActivityProductionProviderTest(unittest.TestCase):
    @patch("backend.app.analysis.providers.collaboration.collect_merge_checks", return_value=())
    @patch("backend.app.analysis.providers.collaboration.build_collaboration_insights")
    @patch("backend.app.analysis.providers.activity.evaluate")
    @patch("backend.app.analysis.providers.activity.collect_commit_history")
    @patch("backend.app.analysis.providers.activity.collect")
    @patch("backend.app.analysis.providers.SourceCraftClient")
    @patch("backend.app.analysis.providers.read_sourcecraft_token", return_value="token")
    def test_production_provider_passes_history_to_activity(
        self,
        _: Mock,
        sourcecraft_client: Mock,
        collect: Mock,
        collect_history: Mock,
        activity_evaluate: Mock,
        build_insights: Mock,
        collect_checks: Mock,
    ) -> None:
        client = Mock()
        sourcecraft_client.return_value = client
        api_facts = build_facts(last_updated=ANALYZED_AT - timedelta(days=1))
        history = CommitHistoryFacts(
            collected=True,
            committed_at=(ANALYZED_AT - timedelta(days=7),),
        )
        collect.return_value = api_facts
        collect_history.return_value = history
        expected = CategoryResult(
            category="activity",
            status=DataStatus.MEASURED,
            score=80,
            summary="Activity measured.",
        )
        activity_evaluate.return_value = expected
        build_insights.return_value = (
            InsightResult(
                code="bus_factor",
                label="Bus factor",
                status=DataStatus.MEASURED,
                value=1,
                summary="Bus factor 1.",
            ),
        )

        registration = next(
            item
            for item in sourcecraft_analyzer_provider(CONTEXT)
            if item.category == "activity"
        )
        result = registration.evaluate(CONTEXT)

        self.assertEqual(result.score, 80)
        self.assertEqual(result.insights[0].code, "bus_factor")
        collect.assert_called_once_with(client, CONTEXT.repository)
        collect_history.assert_called_once_with(CONTEXT, auth_token="token")
        supplied_facts, supplied_context = activity_evaluate.call_args.args
        self.assertEqual(supplied_facts.commit_history, history)
        self.assertIs(supplied_context, CONTEXT)
        collect_checks.assert_called_once()
        build_insights.assert_called_once()
        client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
