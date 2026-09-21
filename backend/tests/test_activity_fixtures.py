"""Контрольные сценарии категории Activity на настоящих ответах SourceCraft."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.analyzers.activity import build_facts, evaluate, parse_datetime
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef

FIXTURES = Path(__file__).resolve().parent / "fixtures"

ANALYZED_AT = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)
PERIOD_START = datetime(2026, 3, 18, 16, 0, tzinfo=UTC)


def load_envelope(label: str, name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / label / f"{name}.json").read_text(encoding="utf-8"))


def analyze(label: str) -> CategoryResult:
    repository_raw = load_envelope(label, "repository")["item"]
    organization = repository_raw.get("organization") or {}
    repository = RepositoryRef(
        id=repository_raw["id"],
        organization_slug=organization.get("slug", ""),
        repository_slug=repository_raw.get("slug", ""),
        web_url=repository_raw.get("web_url"),
    )
    contributors = load_envelope(label, "contributors")
    pulls = load_envelope(label, "pulls")
    releases = load_envelope(label, "releases")
    facts = build_facts(
        last_updated=parse_datetime(repository_raw.get("last_updated")),
        is_empty=bool(repository_raw.get("is_empty")),
        contributor_items=contributors["items"],
        pull_items=pulls["items"],
        release_items=releases["items"],
        contributors_truncated=bool(contributors["_meta"]["truncated"]),
        pulls_truncated=bool(pulls["_meta"]["truncated"]),
        releases_truncated=bool(releases["_meta"]["truncated"]),
        contributors_error=contributors["_meta"].get("error"),
        pulls_error=pulls["_meta"].get("error"),
        releases_error=releases["_meta"].get("error"),
    )
    context = AnalysisContext(
        repository=repository,
        commit_sha="0" * 40,
        analyzed_at=ANALYZED_AT,
        period_start=PERIOD_START,
        period_end=ANALYZED_AT,
    )
    return evaluate(facts, context)


class ActivityOnRealRepositoriesTest(unittest.TestCase):
    def test_live_project_scores_high_and_caps_merge_volume(self) -> None:
        """k-5-45mm/dozzle-plus: свежий репозиторий, много MR и релизов."""
        result = analyze("active")

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertGreater(result.score, 70)
        codes = {metric.code for metric in result.metrics}
        self.assertEqual(
            codes,
            {
                "last_activity_days",
                "merged_mr_in_period",
                "releases_in_period",
                "contributor_count",
            },
        )
        merged = next(metric for metric in result.metrics if metric.code == "merged_mr_in_period")
        self.assertGreater(merged.value, 3)
        self.assertEqual(merged.normalized_score, 100.0)

    def test_stale_project_is_noticeably_worse(self) -> None:
        """brothersandksu/casesc: последнее обновление больше года назад."""
        result = analyze("stale")

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertLess(result.score, 30)
        recency = next(metric for metric in result.metrics if metric.code == "last_activity_days")
        self.assertGreater(recency.value, 180)
        self.assertEqual(recency.normalized_score, 0.0)
        self.assertEqual(
            [item.code for item in result.recommendations],
            ["activity-stale-repository", "activity-mr-not-merged"],
        )

    def test_direct_push_project_is_measured_without_mr_metric(self) -> None:
        """userver/userver: код обновляется, MR и релизы API пусты — это не ноль."""
        result = analyze("no-issues")

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertGreater(result.score, 70)
        self.assertNotIn("merged_mr_in_period", {metric.code for metric in result.metrics})
        self.assertNotIn("releases_in_period", {metric.code for metric in result.metrics})

    def test_open_mrs_without_merge_do_not_count_as_delivery(self) -> None:
        """divkit/divkit: репозиторий свежий, но за период ни один MR не смержен."""
        result = analyze("abandoned-tracker")

        self.assertIs(result.status, DataStatus.MEASURED)
        merged = next(metric for metric in result.metrics if metric.code == "merged_mr_in_period")
        self.assertEqual(merged.value, 0)
        self.assertIn("activity-mr-not-merged", [item.code for item in result.recommendations])

    def test_live_and_stale_are_clearly_separated(self) -> None:
        live = analyze("active")
        stale = analyze("stale")

        self.assertGreater(live.score - stale.score, 50)

    def test_result_is_reproducible(self) -> None:
        first = analyze("active")
        second = analyze("active")

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.summary, second.summary)


if __name__ == "__main__":
    unittest.main()
