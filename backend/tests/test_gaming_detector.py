"""Тесты бонусного детектора накрутки рейтинга (не входит в Score v2)."""

from __future__ import annotations

import inspect
import unittest
from datetime import UTC, datetime, timedelta

from backend.app.analysis.runner import AnalyzerRegistration, run_analysis
from backend.app.analyzers import gaming
from backend.app.analyzers.activity import (
    MERGED_MR_CAP,
    CommitHistoryFacts,
    build_facts,
)
from backend.app.analyzers.activity import (
    evaluate as evaluate_activity,
)
from backend.app.analyzers.gaming import (
    BURST_MIN_COMMITS,
    MR_VOLUME_FLAG_THRESHOLD,
    WARNING_LABEL,
    detect,
)
from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    RepositoryRef,
)
from backend.app.reporting import build_report_payload, render_markdown_report


class GamingDetectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzed_at = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)
        self.period_start = self.analyzed_at - timedelta(days=180)
        self.context = AnalysisContext(
            repository=RepositoryRef("repo-1", "org", "demo", "https://sourcecraft.dev/org/demo"),
            commit_sha="a" * 40,
            analyzed_at=self.analyzed_at,
            period_start=self.period_start,
            period_end=self.analyzed_at,
        )

    def test_burst_detected_in_short_window(self) -> None:
        burst_start = self.analyzed_at - timedelta(days=10)
        burst = tuple(burst_start + timedelta(minutes=i) for i in range(BURST_MIN_COMMITS + 5))
        baseline = tuple(
            self.period_start + timedelta(days=20 * index, hours=3)
            for index in range(1, 6)
        )
        facts = build_facts(
            last_updated=self.analyzed_at,
            pull_items=_pulls(3, self.analyzed_at),
            release_items=_releases(1, self.analyzed_at),
            contributor_items=[{"id": "u1", "username": "alice"}],
            commit_history=CommitHistoryFacts(
                collected=True,
                committed_at=burst + baseline,
            ),
        )

        result = detect(facts, self.context)
        burst_signal = next(signal for signal in result.signals if signal.code == "burst")

        self.assertTrue(result.suspected)
        self.assertEqual(result.label, WARNING_LABEL)
        self.assertIs(burst_signal.status, DataStatus.MEASURED)
        self.assertTrue(burst_signal.flagged)

    def test_high_mr_volume_flagged_while_activity_score_stays_at_cap(self) -> None:
        merged_count = MR_VOLUME_FLAG_THRESHOLD
        facts = build_facts(
            last_updated=self.analyzed_at - timedelta(days=1),
            pull_items=_pulls(merged_count, self.analyzed_at),
            release_items=_releases(1, self.analyzed_at),
            contributor_items=[
                {"id": "u1", "username": "alice"},
                {"id": "u2", "username": "bob"},
                {"id": "u3", "username": "carol"},
            ],
            commit_history=CommitHistoryFacts(
                collected=True,
                committed_at=tuple(
                    self.period_start + timedelta(weeks=index)
                    for index in range(8)
                ),
            ),
        )

        activity_result = evaluate_activity(facts, self.context)
        gaming_result = detect(facts, self.context)
        merged_metric = next(
            metric for metric in activity_result.metrics if metric.code == "merged_mr_in_period"
        )
        mr_signal = next(signal for signal in gaming_result.signals if signal.code == "mr_volume")

        self.assertIs(activity_result.status, DataStatus.MEASURED)
        self.assertEqual(merged_metric.value, merged_count)
        self.assertEqual(merged_metric.normalized_score, 100.0)
        self.assertGreaterEqual(merged_count, MERGED_MR_CAP)
        self.assertTrue(mr_signal.flagged)
        self.assertTrue(gaming_result.suspected)

    def test_truncated_history_does_not_flag_burst(self) -> None:
        burst_start = self.analyzed_at - timedelta(days=3)
        facts = build_facts(
            last_updated=self.analyzed_at,
            pull_items=_pulls(2, self.analyzed_at),
            commit_history=CommitHistoryFacts(
                collected=True,
                committed_at=tuple(
                    burst_start + timedelta(minutes=i) for i in range(BURST_MIN_COMMITS + 10)
                ),
                truncated=True,
            ),
        )

        result = detect(facts, self.context)
        burst_signal = next(signal for signal in result.signals if signal.code == "burst")

        self.assertIs(burst_signal.status, DataStatus.UNAVAILABLE)
        self.assertFalse(burst_signal.flagged)
        self.assertFalse(result.suspected)

    def test_missing_history_is_unavailable_not_clean(self) -> None:
        facts = build_facts(
            last_updated=self.analyzed_at,
            pull_items=_pulls(2, self.analyzed_at),
            commit_history=CommitHistoryFacts(collected=True, error="commit_history_unavailable"),
        )

        result = detect(facts, self.context)
        burst_signal = next(signal for signal in result.signals if signal.code == "burst")

        self.assertIs(burst_signal.status, DataStatus.UNAVAILABLE)
        self.assertFalse(result.suspected)
        self.assertIn("не означает", result.summary.lower())

    def test_report_score_identical_with_and_without_warning(self) -> None:
        activity_score = 88.0

        def activity_plain(_: AnalysisContext) -> CategoryResult:
            return CategoryResult(
                category="activity",
                status=DataStatus.MEASURED,
                score=activity_score,
                summary="Активность измерена.",
            )

        def activity_with_gaming(ctx: AnalysisContext) -> CategoryResult:
            gaming.record_detection(
                detect(
                    build_facts(
                        last_updated=self.analyzed_at,
                        pull_items=_pulls(MR_VOLUME_FLAG_THRESHOLD, self.analyzed_at),
                        commit_history=CommitHistoryFacts(collected=True),
                    ),
                    ctx,
                )
            )
            return CategoryResult(
                category="activity",
                status=DataStatus.MEASURED,
                score=activity_score,
                summary="Активность измерена.",
            )

        plain = run_analysis(self.context, (AnalyzerRegistration("activity", activity_plain),))
        with_warning = run_analysis(
            self.context, (AnalyzerRegistration("activity", activity_with_gaming),)
        )

        plain_report = build_report_payload(plain, analysis_id="analysis-plain")
        warned_report = build_report_payload(with_warning, analysis_id="analysis-warned")
        markdown = render_markdown_report(with_warning, analysis_id="analysis-warned")

        self.assertEqual(plain.analysis.score, with_warning.analysis.score)
        self.assertEqual(plain.score_summary.coverage, with_warning.score_summary.coverage)
        self.assertEqual(plain_report["score"], warned_report["score"])
        self.assertNotIn("gamingWarning", plain_report)
        self.assertIn("gamingWarning", warned_report)
        self.assertTrue(warned_report["gamingWarning"]["suspected"])
        self.assertEqual(warned_report["gamingWarning"]["label"], WARNING_LABEL)
        self.assertIn(WARNING_LABEL, markdown)
        self.assertIn("не меняет Repo Health Score", markdown)

    def test_likes_are_not_an_input(self) -> None:
        signature = inspect.signature(detect)
        parameters = tuple(signature.parameters)

        self.assertEqual(parameters, ("facts", "context"))
        self.assertNotIn("likes", parameters)
        self.assertNotIn("rating", parameters)
        self.assertFalse(hasattr(build_facts(last_updated=self.analyzed_at), "likes"))
        # Сигнатура и факты Activity — единственные входы; лайки туда не передаются.
        detect(build_facts(last_updated=self.analyzed_at), self.context)


def _pulls(merged_count: int, moment: datetime) -> list[dict[str, object]]:
    return [
        {
            "slug": f"mr-{index}",
            "title": f"MR {index}",
            "status": "merged",
            "created_at": (moment - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
            "updated_at": (moment - timedelta(hours=index)).isoformat().replace("+00:00", "Z"),
        }
        for index in range(merged_count)
    ]


def _releases(count: int, moment: datetime) -> list[dict[str, object]]:
    return [
        {
            "tag": f"v1.{index}",
            "title": f"Release {index}",
            "status": "published",
            "released_at": (moment - timedelta(days=index + 1)).isoformat().replace("+00:00", "Z"),
            "is_pre_release": False,
        }
        for index in range(count)
    ]


if __name__ == "__main__":
    unittest.main()
