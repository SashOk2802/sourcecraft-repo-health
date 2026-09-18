"""Контрольные сценарии категории Issues на настоящих ответах SourceCraft.

Фикстуры сняты скриптом scripts/fetch_fixtures.py. Момент анализа зафиксирован
константой: время — входной параметр методики, поэтому результат не меняется
со временем и остаётся воспроизводимым.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.analyzers.issues import build_facts, evaluate
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Фикстуры сняты 18 сентября 2026 года; расчёт привязан к этому моменту.
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

    open_envelope = load_envelope(label, "issues_open")
    closed_envelope = load_envelope(label, "issues_closed")
    facts = build_facts(
        open_envelope["items"],
        closed_envelope["items"],
        open_truncated=bool(open_envelope["_meta"]["truncated"]),
        closed_truncated=bool(closed_envelope["_meta"]["truncated"]),
        # Скрипт сбора записывает сбой источника в _meta.error; без переноса в факты
        # пустой ответ после ошибки выглядел бы как «задач нет».
        open_error=open_envelope["_meta"].get("error"),
        closed_error=closed_envelope["_meta"].get("error"),
    )

    context = AnalysisContext(
        repository=repository,
        commit_sha="0" * 40,
        analyzed_at=ANALYZED_AT,
        period_start=PERIOD_START,
        period_end=ANALYZED_AT,
    )
    return evaluate(facts, context)


class IssuesOnRealRepositoriesTest(unittest.TestCase):
    """Проверяет, что методика ведёт себя логично на четырёх разных репозиториях."""

    def test_repository_without_tracker_is_not_scored(self) -> None:
        """userver/userver: задач нет вообще — это не ноль баллов."""
        result = analyze("no-issues")

        self.assertIs(result.status, DataStatus.NOT_APPLICABLE)
        self.assertIsNone(result.score)
        self.assertIsNotNone(result.reason)

    def test_popular_project_with_abandoned_tracker_scores_zero(self) -> None:
        """divkit/divkit: код обновляется, но ни одна из 16 задач не двигалась год."""
        result = analyze("abandoned-tracker")

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 0.0)

        stale = next(metric for metric in result.metrics if metric.code == "stale_open_ratio")
        self.assertEqual(stale.value, 1.0)
        # completed_at у этого проекта не заполнен, поэтому время до закрытия не измеряется.
        self.assertNotIn("median_days_to_close", {metric.code for metric in result.metrics})

    def test_abandoned_tracker_evidence_links_to_sourcecraft(self) -> None:
        result = analyze("abandoned-tracker")

        stale = next(metric for metric in result.metrics if metric.code == "stale_open_ratio")
        self.assertTrue(stale.evidence)
        for item in stale.evidence:
            self.assertIsNotNone(item.url)
            self.assertTrue(
                item.url.startswith("https://sourcecraft.dev/divkit/divkit/issues/"),
                msg=item.url,
            )

    def test_small_stale_project_scores_zero(self) -> None:
        """brothersandksu/casesc: четыре открытые задачи, последняя активность год назад."""
        result = analyze("stale")

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 0.0)

    def test_live_tracker_scores_high_and_measures_every_metric(self) -> None:
        """k-5-45mm/dozzle-plus: брошенных задач нет, медиана закрытия — дни."""
        result = analyze("active")

        self.assertIs(result.status, DataStatus.MEASURED)
        self.assertGreater(result.score, 70)
        self.assertEqual(
            {metric.code for metric in result.metrics},
            {"stale_open_ratio", "backlog_trend", "median_days_to_close"},
        )

        stale = next(metric for metric in result.metrics if metric.code == "stale_open_ratio")
        self.assertEqual(stale.value, 0.0)

        # Брошенных задач нет, поэтому P1 не возникает; но решается меньше половины
        # поступающих, и это честно отмечается рекомендацией среднего приоритета.
        self.assertEqual(
            [recommendation.code for recommendation in result.recommendations],
            ["issues-backlog-growing"],
        )

    def test_live_and_abandoned_trackers_are_clearly_separated(self) -> None:
        """Существенная проблема должна заметно менять оценку, а не сдвигать её чуть-чуть."""
        live = analyze("active")
        abandoned = analyze("abandoned-tracker")

        self.assertGreater(live.score - abandoned.score, 50)

    def test_result_is_reproducible_on_the_same_fixtures(self) -> None:
        first = analyze("active")
        second = analyze("active")

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.summary, second.summary)


if __name__ == "__main__":
    unittest.main()
