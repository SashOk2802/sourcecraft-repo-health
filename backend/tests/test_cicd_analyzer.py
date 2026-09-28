"""Проверяет формулу CI/CD и честные статусы без обращения к SourceCraft."""

from __future__ import annotations

import json
import unittest
from asyncio import CancelledError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from backend.app.analysis.runner import AnalyzerRegistration, run_analysis
from backend.app.analyzers.cicd import (
    CATEGORY_CODE,
    MINIMUM_AUTOMATED_RUNS,
    CicdFacts,
    CiRunFact,
    build_facts,
    evaluate,
    make_analyzer,
)
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef


class CicdAnalyzerTest(unittest.TestCase):
    def test_repository_event_is_valid_but_not_scored_as_automated(self) -> None:
        run = CiRunFact(
            slug="repository-event-run",
            status="success",
            event_type="repository_event",
            created_at=_timestamp(),
        )

        self.assertEqual(run.event_type, "repository_event")
        self.assertFalse(run.is_automated)

    def test_repository_event_does_not_change_push_sample_or_score(self) -> None:
        for status in ("success", "failed", "timeout", "processing"):
            with self.subTest(status=status):
                facts = build_facts(
                    (
                        *(_run(str(index), "success") for index in range(1, 6)),
                        _run("repository-event-run", status, event_type="repository_event"),
                    )
                )

                result = evaluate(facts, _context())

                self.assertEqual(result.status, DataStatus.MEASURED)
                self.assertEqual(result.score, 100)
                self.assertEqual(result.metrics[0].value, 5)
                self.assertEqual(result.metrics[1].value, 100)
                self.assertEqual(result.recommendations, ())

    def test_repository_events_do_not_fill_minimum_automated_sample(self) -> None:
        for push_count in (0, 4):
            with self.subTest(push_count=push_count):
                facts = build_facts(
                    (
                        *(_run(f"push-{index}", "success") for index in range(push_count)),
                        *(
                            _run(f"event-{index}", "success", event_type="repository_event")
                            for index in range(5)
                        ),
                    )
                )

                result = evaluate(facts, _context())

                self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
                self.assertIsNone(result.score)
                self.assertEqual(result.recommendations, ())

    def test_provider_can_build_mixed_event_history_without_losing_cicd_category(self) -> None:
        def provider(_: RepositoryRef) -> CicdFacts:
            # Создание фактов внутри поставщика повторяет путь будущего адаптера.
            return build_facts(
                (
                    *(_run(str(index), "success") for index in range(1, 6)),
                    _run("repository-event-run", "failed", event_type="repository_event"),
                )
            )

        execution = run_analysis(
            _context(),
            (AnalyzerRegistration("cicd", make_analyzer(provider)),),
        )
        result = next(
            category for category in execution.analysis.categories if category.category == "cicd"
        )

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 100)
        self.assertEqual(result.metrics[0].value, 5)
        self.assertEqual(execution.analysis.score, 100)
        self.assertEqual(execution.score_summary.coverage, 0.2)

    def test_measures_success_rate_from_automated_outcome_runs(self) -> None:
        facts = build_facts(
            (
                _run("1", "success"),
                _run("2", "success"),
                _run("3", "success"),
                _run("4", "success"),
                _run("5", "failed"),
                _run("6", "canceled"),
                _run("7", "success", event_type="manual"),
                _run("8", "processing"),
            )
        )

        result = evaluate(facts, _context())

        self.assertEqual(result.category, CATEGORY_CODE)
        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 80)
        self.assertEqual(result.metrics[0].code, "automated_ci_outcome_runs")
        self.assertEqual(result.metrics[0].value, MINIMUM_AUTOMATED_RUNS)
        self.assertEqual(result.metrics[1].value, 80)
        self.assertEqual(result.metrics[1].normalized_score, 80)
        self.assertEqual(result.recommendations[0].priority.value, "p2")
        runs_url = "https://sourcecraft.dev/example-org/example-repo/cicd/runs"
        failed_url = f"{runs_url}/5"
        self.assertEqual(result.metrics[0].evidence[0].url, runs_url)
        self.assertEqual(
            tuple(evidence.url for evidence in result.metrics[1].evidence),
            (runs_url, failed_url),
        )
        self.assertEqual(result.recommendations[0].evidence[0].url, failed_url)
        self.assertNotIn("1", result.summary)

    def test_links_only_recent_failed_automated_outcomes_in_period(self) -> None:
        context = _context()
        facts = build_facts(
            (
                _run("success", "success"),
                _run("older", "failed", created_at=_timestamp() - timedelta(days=1)),
                _run("timeout", "timeout", created_at=_timestamp() + timedelta(days=1)),
                _run("failed-a", "failed", created_at=_timestamp() + timedelta(days=2)),
                _run("failed-b", "failed", created_at=_timestamp() + timedelta(days=3)),
                _run("manual", "failed", event_type="manual"),
                _run("outside", "failed", created_at=context.period_start - timedelta(days=1)),
                _run("unfinished", "processing"),
            )
        )

        result = evaluate(facts, context)

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 20)
        self.assertEqual(
            tuple(evidence.reference for evidence in result.recommendations[0].evidence),
            ("ci-run-failed-b", "ci-run-failed-a", "ci-run-timeout"),
        )
        self.assertEqual(len(result.metrics[1].evidence), 4)
        self.assertNotIn("manual", repr(result.recommendations[0].evidence))
        self.assertNotIn("outside", repr(result.recommendations[0].evidence))

    def test_unsafe_url_segments_do_not_produce_links(self) -> None:
        context = _context()
        unsafe_repository = AnalysisContext(
            repository=RepositoryRef("id", "example-org", "repo/../other"),
            commit_sha=context.commit_sha,
            analyzed_at=context.analyzed_at,
            period_start=context.period_start,
            period_end=context.period_end,
        )
        facts = build_facts(
            (
                *(_run(str(index), "success") for index in range(1, 5)),
                _run("bad/slug", "failed"),
            )
        )

        safe_repository_result = evaluate(facts, context)
        unsafe_repository_result = evaluate(facts, unsafe_repository)

        self.assertEqual(safe_repository_result.recommendations[0].evidence, ())
        self.assertIsNone(unsafe_repository_result.metrics[0].evidence[0].url)
        self.assertEqual(unsafe_repository_result.recommendations[0].evidence, ())

    def test_marks_low_success_rate_as_p1(self) -> None:
        result = evaluate(
            build_facts(tuple(_run(str(index), "failed") for index in range(1, 6))),
            _context(),
        )

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 0)
        self.assertEqual(result.recommendations[0].priority.value, "p1")

    def test_excludes_runs_outside_context_period(self) -> None:
        before_period = _context().period_start - timedelta(seconds=1)
        after_period = _context().period_end + timedelta(seconds=1)
        facts = build_facts(
            (
                *(_run(str(index), "success") for index in range(1, 6)),
                _run("old", "failed", created_at=before_period),
                _run("future", "failed", created_at=after_period),
            )
        )

        result = evaluate(facts, _context())

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 100)
        self.assertEqual(result.recommendations, ())

    def test_empty_history_is_not_claimed_as_no_need_for_cicd(self) -> None:
        result = evaluate(build_facts(()), _context())

        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertEqual(result.reason, "cicd_no_runs")
        self.assertIsNone(result.score)

    def test_manual_or_unfinished_runs_are_not_a_reliability_sample(self) -> None:
        facts = build_facts(
            (
                _run("1", "success", event_type="manual"),
                _run("2", "failed", event_type="restart"),
                _run("3", "processing"),
                _run("4", "awaiting_approval"),
                _run("5", "canceled"),
                _run("6", "rejected"),
                _run("7", "skipped"),
            )
        )

        result = evaluate(facts, _context())

        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertEqual(result.reason, "cicd_too_few_outcome_runs")
        self.assertIsNone(result.score)

    def test_anonymized_sourcecraft_manual_run_does_not_create_a_score(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "sourcecraft" / "ci_runs.json"
        raw_run = json.loads(fixture_path.read_text(encoding="utf-8"))[0]
        created_at = datetime.fromisoformat(raw_run["dates"]["created_at"])
        facts = build_facts(
            (
                CiRunFact(
                    slug=raw_run["slug"],
                    status=raw_run["status"],
                    event_type=raw_run["event_type"],
                    created_at=created_at,
                ),
            )
        )

        result = evaluate(facts, _context())

        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertEqual(result.reason, "cicd_no_automated_runs_in_period")
        self.assertIsNone(result.score)

    def test_too_few_automated_outcomes_do_not_produce_a_score(self) -> None:
        result = evaluate(
            build_facts(tuple(_run(str(index), "success") for index in range(1, 5))),
            _context(),
        )

        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertEqual(result.reason, "cicd_too_few_outcome_runs")
        self.assertIsNone(result.score)

    def test_source_error_and_missing_data_are_unavailable_without_leak(self) -> None:
        for facts in (
            build_facts(None),
            build_facts(None, source_error="Bearer synthetic-token-marker"),
        ):
            with self.subTest(facts=facts):
                result = evaluate(facts, _context())

                self.assertEqual(result.status, DataStatus.UNAVAILABLE)
                self.assertEqual(result.reason, "cicd_runs_unavailable")
                self.assertIsNone(result.score)
                self.assertNotIn("synthetic-token-marker", repr(result))

    def test_truncated_history_is_not_scored(self) -> None:
        result = evaluate(build_facts((_run("1", "success"),), truncated=True), _context())

        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertEqual(result.reason, "cicd_runs_truncated")
        self.assertEqual(result.metrics[0].value, "partial")

    def test_facts_reject_error_with_runs_truncation_or_duplicates(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            build_facts((), source_error="source failed")
        with self.assertRaisesRegex(ValueError, "requires collected runs"):
            build_facts(None, truncated=True)
        with self.assertRaisesRegex(ValueError, "duplicate slugs"):
            build_facts((_run("1", "success"), _run("1", "failed")))

    def test_run_rejects_blank_slug_unknown_values_and_naive_time(self) -> None:
        base = {"slug": "1", "status": "success", "event_type": "push", "created_at": _timestamp()}
        cases = (
            ({"slug": " "}, "slug"),
            ({"status": "unexpected"}, "status"),
            ({"event_type": "unexpected"}, "event type"),
            ({"created_at": _timestamp().replace(tzinfo=None)}, "timezone"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                CiRunFact(**(base | changes))

    def test_facts_reject_invalid_direct_inputs_and_hide_raw_error(self) -> None:
        with self.assertRaisesRegex(TypeError, "tuple"):
            CicdFacts(runs=[_run("1", "success")])
        with self.assertRaisesRegex(TypeError, "boolean"):
            CicdFacts(runs=(), truncated="yes")
        facts = CicdFacts(source_error="Bearer synthetic-token-marker")
        self.assertNotIn("synthetic-token-marker", repr(facts))

    def test_factory_passes_current_repository_and_safely_handles_provider_error(self) -> None:
        received: list[RepositoryRef] = []

        def provider(repository: RepositoryRef) -> CicdFacts:
            received.append(repository)
            return build_facts(())

        result = make_analyzer(provider)(_context())
        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertEqual(received, [_context().repository])

        failing_provider = Mock(side_effect=RuntimeError("Bearer synthetic-token-marker"))
        with self.assertLogs("backend.app.analyzers.cicd", level="WARNING") as logs:
            failed_result = make_analyzer(failing_provider)(_context())
        self.assertEqual(failed_result.status, DataStatus.UNAVAILABLE)
        self.assertNotIn("synthetic-token-marker", "\n".join(logs.output))
        self.assertTrue(all(record.exc_info is None for record in logs.records))

    def test_factory_does_not_hide_cancellation_or_process_stop(self) -> None:
        for exception in (CancelledError, KeyboardInterrupt, SystemExit):
            with self.subTest(exception=exception):
                provider = Mock(side_effect=exception())
                with self.assertRaises(exception):
                    make_analyzer(provider)(_context())

    def test_measured_cicd_result_contributes_to_common_score(self) -> None:
        provider = Mock(
            return_value=build_facts(tuple(_run(str(index), "success") for index in range(1, 6)))
        )
        execution = run_analysis(
            _context(),
            (
                AnalyzerRegistration("cicd", make_analyzer(provider)),
                AnalyzerRegistration(
                    "activity",
                    lambda _: _measured_result("activity", 60),
                ),
            ),
        )

        cicd = next(
            category
            for category in execution.analysis.categories
            if category.category == CATEGORY_CODE
        )
        self.assertEqual(cicd.status, DataStatus.MEASURED)
        self.assertEqual(cicd.score, 100)
        self.assertAlmostEqual(execution.analysis.score, 100 / 35 * 20 + 60 / 35 * 15)
        self.assertEqual(execution.score_summary.coverage, 0.35)


def _timestamp() -> datetime:
    return datetime(2026, 1, 15, tzinfo=UTC)


def _run(
    slug: str,
    status: str,
    *,
    event_type: str = "push",
    created_at: datetime | None = None,
) -> CiRunFact:
    return CiRunFact(
        slug=slug,
        status=status,
        event_type=event_type,
        created_at=created_at or _timestamp(),
    )


def _context() -> AnalysisContext:
    analyzed_at = datetime(2026, 2, 1, tzinfo=UTC)
    return AnalysisContext(
        repository=RepositoryRef("repository-id-redacted", "example-org", "example-repo"),
        commit_sha="commit-sha-redacted",
        analyzed_at=analyzed_at,
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=analyzed_at,
    )


def _measured_result(category: str, score: float) -> CategoryResult:
    return CategoryResult(
        category=category,
        status=DataStatus.MEASURED,
        score=score,
        summary=f"Результат {category}.",
    )
