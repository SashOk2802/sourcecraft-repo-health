"""Контрольные сценарии категории Issues на настоящих ответах SourceCraft.

Фикстуры сняты скриптом scripts/fetch_fixtures.py. Момент анализа зафиксирован
константой: время — входной параметр методики, поэтому результат не меняется
со временем и остаётся воспроизводимым.
"""

from __future__ import annotations

import json
import tempfile
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


def issue_fixture_dirs(fixtures_root: Path = FIXTURES) -> list[Path]:
    """Наборы scripts/fetch_fixtures.py. Другие JSON в fixtures/ сюда не входят."""
    return sorted(
        path
        for path in fixtures_root.iterdir()
        if path.is_dir() and (path / "issues_open.json").is_file()
    )


def _collect_forbidden_keys(
    payload: Any,
    forbidden: set[str],
    prefix: str,
    offenders: list[str],
) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            location = f"{prefix}.{key}"
            if key in forbidden:
                offenders.append(location)
            _collect_forbidden_keys(value, forbidden, location, offenders)
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _collect_forbidden_keys(item, forbidden, f"{prefix}[{index}]", offenders)


def fixture_pii_offenders(fixtures_root: Path = FIXTURES) -> list[str]:
    """Возвращает запрещённые поля только из наборов фикстур категории Issues."""
    forbidden = {
        "author",
        "avatar",
        "bio",
        "city",
        "clone_url",
        "description",
        "display_name",
        "links",
        "location",
        "release_notes",
        "updated_by",
    }
    offenders: list[str] = []
    for directory in issue_fixture_dirs(fixtures_root):
        for path in directory.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            _collect_forbidden_keys(
                payload,
                forbidden,
                str(path.relative_to(fixtures_root)),
                offenders,
            )
    return offenders


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
    in_progress_envelope = load_envelope(label, "issues_in_progress")
    closed_envelope = load_envelope(label, "issues_closed")
    open_errors = [
        error
        for error in (
            open_envelope["_meta"].get("error"),
            in_progress_envelope["_meta"].get("error"),
        )
        if error
    ]
    facts = build_facts(
        list(open_envelope["items"]) + list(in_progress_envelope["items"]),
        closed_envelope["items"],
        open_truncated=bool(open_envelope["_meta"]["truncated"])
        or bool(in_progress_envelope["_meta"]["truncated"]),
        closed_truncated=bool(closed_envelope["_meta"]["truncated"]),
        # Скрипт сбора записывает сбой источника в _meta.error; без переноса в факты
        # пустой ответ после ошибки выглядел бы как «задач нет».
        open_error="; ".join(open_errors) or None,
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
        """divkit/divkit: код обновляется, но открытые и in_progress задачи стоят год."""
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

    def test_loader_merges_in_progress_with_open(self) -> None:
        """Production-анализатор складывает open и in_progress; фикстуры должны делать так же."""
        open_items = load_envelope("abandoned-tracker", "issues_open")["items"]
        in_progress_items = load_envelope("abandoned-tracker", "issues_in_progress")["items"]
        closed_items = load_envelope("abandoned-tracker", "issues_closed")["items"]

        self.assertTrue(in_progress_items)
        facts = build_facts(list(open_items) + list(in_progress_items), closed_items)

        self.assertEqual(len(facts.open_issues), len(open_items) + len(in_progress_items))

    def test_fixture_payloads_omit_personal_and_sensitive_fields(self) -> None:
        directories = issue_fixture_dirs()
        self.assertTrue(directories)
        self.assertEqual(fixture_pii_offenders(), [])

    def test_pii_scan_ignores_unrelated_fixture_trees(self) -> None:
        """Чужие JSON в fixtures/ (на CI это sourcecraft/) не должны валить проверку PII."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixtures_root = Path(temporary_directory)
            issue_dir = fixtures_root / "issues"
            issue_dir.mkdir()
            (issue_dir / "issues_open.json").write_text('{"items": []}', encoding="utf-8")
            alien_dir = fixtures_root / "_unrelated-pii-scan"
            alien_dir.mkdir()
            (alien_dir / "repositories_page.json").write_text(
                '{"repositories": [{"description": "x", "clone_url": {}, "links": []}]}',
                encoding="utf-8",
            )

            self.assertNotIn(alien_dir, issue_fixture_dirs(fixtures_root))
            self.assertEqual(fixture_pii_offenders(fixtures_root), [])

    def test_contributors_keep_only_identity_fields(self) -> None:
        allowed = {"id", "username"}
        for directory in issue_fixture_dirs():
            path = directory / "contributors.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            for index, item in enumerate(payload["items"]):
                extra = set(item) - allowed
                self.assertEqual(
                    extra,
                    set(),
                    msg=f"{directory.name} contributors[{index}]: {sorted(extra)}",
                )


if __name__ == "__main__":
    unittest.main()
