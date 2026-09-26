"""Проверяет честную обработку недоступных и неподготовленных AppSec-данных."""

from __future__ import annotations

import json
import unittest
from asyncio import CancelledError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from backend.app.analysis.runner import AnalyzerRegistration, run_analysis
from backend.app.analyzers.security import (
    CATEGORY_CODE,
    SecurityFacts,
    appsec_payload_status,
    build_facts,
    evaluate,
    make_analyzer,
)
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef


class SecurityAnalyzerTest(unittest.TestCase):
    def test_null_payload_is_unavailable_and_never_gets_a_score(self) -> None:
        result = evaluate(build_facts(None))

        self.assertEqual(result.category, CATEGORY_CODE)
        self.assertEqual(result.status, DataStatus.UNAVAILABLE)
        self.assertIsNone(result.score)
        self.assertEqual(result.reason, "appsec_unavailable")
        self.assertEqual(result.metrics[0].value, "unavailable")
        self.assertEqual(result.metrics[0].evidence[0].source, "sourcecraft-appsec")
        self.assertNotIn("findings", result.metrics[0].evidence[0].summary)

    def test_payload_without_confirmed_coverage_does_not_get_a_score(self) -> None:
        for payload in ([], {}, {"defects": []}, [{"severity": "critical"}]):
            with self.subTest(payload=payload):
                result = evaluate(build_facts(payload))

                self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
                self.assertIsNone(result.score)
                self.assertEqual(result.reason, "appsec_coverage_not_confirmed")
                self.assertEqual(result.metrics[0].value, "received")
                self.assertIsNone(result.metrics[0].normalized_score)
                self.assertEqual(result.recommendations, ())

    def test_observed_limited_sast_summary_does_not_become_a_security_score(self) -> None:
        summary_path = Path(__file__).parent / "fixtures/sourcecraft/appsec_sast_summary.json"
        payload = json.loads(summary_path.read_text(encoding="utf-8"))

        result = evaluate(build_facts(payload))

        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        self.assertEqual(result.reason, "appsec_coverage_not_confirmed")
        self.assertEqual(result.metrics[0].value, "received")
        self.assertNotIn("23", repr(result))
        self.assertNotIn("HIGH", repr(result))
        self.assertNotIn("LOW", repr(result))
        self.assertNotIn("MEDIUM", repr(result))

    def test_complete_appsec_payload_without_open_findings_scores_100(self) -> None:
        result = evaluate(build_facts(_complete_payload()))

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 100)
        self.assertEqual(result.reason, "security_score_v1")
        self.assertEqual(result.metrics[0].code, "appsec_data_coverage")
        self.assertEqual(result.metrics[0].value, "complete")
        self.assertEqual(result.metrics[1].code, "appsec_open_findings")
        self.assertEqual(result.metrics[1].value, 0)
        self.assertEqual(result.recommendations, ())

    def test_confirmed_open_critical_reduces_security_and_caps_overall_score(self) -> None:
        security_result = evaluate(
            build_facts(
                _complete_payload(
                    {"severity": "CRITICAL", "status": "TRIAGED_TP", "count": 1},
                )
            )
        )
        execution = _execution_with_security(security_result)

        self.assertEqual(security_result.score, 40)
        self.assertEqual(
            next(
                metric.value
                for metric in security_result.metrics
                if metric.code == "appsec_confirmed_open_critical_findings"
            ),
            1,
        )
        self.assertEqual(execution.score_summary.uncapped_score, 77.5)
        self.assertEqual(execution.score_summary.score, 60)
        self.assertEqual(execution.score_summary.score_limit.code, "security-open-critical")
        self.assertEqual(security_result.recommendations[0].priority.value, "p0")

    def test_untriaged_open_critical_does_not_apply_global_cap(self) -> None:
        security_result = evaluate(
            build_facts(
                _complete_payload(
                    {"severity": "CRITICAL", "status": "OPEN", "count": 1},
                )
            )
        )
        execution = _execution_with_security(security_result)

        self.assertEqual(security_result.score, 40)
        self.assertEqual(execution.score_summary.uncapped_score, 77.5)
        self.assertEqual(execution.score_summary.score, 77.5)
        self.assertIsNone(execution.score_summary.score_limit)

    def test_resolved_findings_do_not_reduce_security_score(self) -> None:
        result = evaluate(
            build_facts(
                _complete_payload(
                    {"severity": "CRITICAL", "status": "RESOLVED_FP", "count": 1},
                    {"severity": "HIGH", "status": "RESOLVED_TOLERABLE", "count": 3},
                )
            )
        )

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 100)
        self.assertEqual(result.recommendations, ())

    def test_open_findings_are_bounded_by_severity_in_formula(self) -> None:
        result = evaluate(
            build_facts(
                _complete_payload(
                    {"severity": "HIGH", "status": "OPEN", "count": 5},
                    {"severity": "MEDIUM", "status": "OPEN", "count": 5},
                    {"severity": "LOW", "status": "OPEN", "count": 20},
                )
            )
        )

        # 100 - 15*min(5, 3) - 5*min(5, 4) - min(20, 10) = 25.
        self.assertEqual(result.score, 25)
        self.assertEqual(
            [item.code for item in result.recommendations],
            ["appsec-open-high", "appsec-open-medium", "appsec-open-low"],
        )

    def test_unknown_group_value_does_not_get_score_or_leak_into_result(self) -> None:
        payload = _complete_payload(
            {"severity": "HIGH", "status": "OPEN", "count": 1},
        )
        engines = payload["engines"]
        assert isinstance(engines, list)
        groups = engines[0]["finding_groups"]
        assert isinstance(groups, list)
        groups[0]["status"] = "synthetic-unknown-status-marker"

        result = evaluate(build_facts(payload))

        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        self.assertNotIn("synthetic-unknown-status-marker", repr(result))

    def test_source_error_is_not_disguised_as_unavailable(self) -> None:
        source_error = "SourceCraft timed out with token-that-must-not-be-reported"
        result = evaluate(build_facts(None, source_error=source_error))

        self.assertEqual(result.status, DataStatus.ERROR)
        self.assertIsNone(result.score)
        self.assertEqual(result.reason, "appsec_source_error")
        self.assertNotIn(source_error, result.summary)
        self.assertNotIn(source_error, result.metrics[0].summary)

    def test_facts_reject_conflicting_payload_and_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be provided together"):
            build_facts([], source_error="SourceCraft timed out")

    def test_facts_reject_blank_source_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be blank"):
            build_facts(None, source_error="   ")

    def test_both_constructors_reject_invalid_or_conflicting_errors(self) -> None:
        for constructor in (SecurityFacts, build_facts):
            for error in ("", " \t\n"):
                with (
                    self.subTest(constructor=constructor.__name__, error=error),
                    self.assertRaisesRegex(ValueError, "must not be blank"),
                ):
                    constructor(None, source_error=error)
            for error in (False, 0, [], {}, RuntimeError("synthetic-error-marker")):
                with (
                    self.subTest(constructor=constructor.__name__, error_type=type(error)),
                    self.assertRaisesRegex(TypeError, "source_error must be a string"),
                ):
                    constructor(None, source_error=error)
            for payload in ([], {}, {"defects": []}):
                with (
                    self.subTest(constructor=constructor.__name__, payload=payload),
                    self.assertRaisesRegex(ValueError, "cannot be provided together"),
                ):
                    constructor(payload, source_error="synthetic-error-marker")

    def test_invalid_payload_types_are_rejected_without_echoing_them(self) -> None:
        for validate in (SecurityFacts, build_facts, appsec_payload_status):
            for payload in (False, 0, 1.5, "synthetic-payload-marker", b"bytes", (), object()):
                with self.subTest(validate=validate.__name__, payload_type=type(payload)):
                    with self.assertRaises(TypeError) as caught:
                        validate(payload)
                    self.assertNotIn("synthetic-payload-marker", str(caught.exception))

    def test_facts_repr_does_not_expose_raw_appsec_data_or_errors(self) -> None:
        for facts in (
            build_facts({"secret": "synthetic-payload-marker"}),
            build_facts([{"snippet": "synthetic-payload-marker"}]),
            build_facts(None, source_error="synthetic-error-marker"),
        ):
            with self.subTest(facts_type=type(facts)):
                self.assertNotIn("synthetic-payload-marker", repr(facts))
                self.assertNotIn("synthetic-error-marker", repr(facts))

    def test_factory_converts_provider_exception_to_safe_error(self) -> None:
        provider = Mock(side_effect=RuntimeError("synthetic-error-marker"))

        with self.assertLogs("backend.app.analyzers.security", level="WARNING") as logs:
            result = make_analyzer(provider)(_context())

        self.assertEqual(result.status, DataStatus.ERROR)
        self.assertEqual(result.reason, "appsec_source_error")
        self.assertIsNone(result.score)
        self.assertNotIn("synthetic-error-marker", repr(result))
        self.assertNotIn("synthetic-error-marker", "\n".join(logs.output))
        self.assertTrue(all(record.exc_info is None for record in logs.records))
        provider.assert_called_once_with(_context().repository)

    def test_factory_rejects_wrong_provider_result_as_error(self) -> None:
        for payload in (None, [], {"secret": "synthetic-payload-marker"}, "invalid", 0):
            with self.subTest(payload_type=type(payload)):
                provider = Mock(return_value=payload)
                with self.assertLogs("backend.app.analyzers.security", level="WARNING") as logs:
                    result = make_analyzer(provider)(_context())
                self.assertEqual(result.status, DataStatus.ERROR)
                self.assertEqual(result.reason, "appsec_source_error")
                self.assertIsNone(result.score)
                self.assertNotIn("synthetic-payload-marker", "\n".join(logs.output))
                self.assertNotIn("synthetic-payload-marker", repr(result))

    def test_factory_does_not_swallow_process_interruption_or_cancellation(self) -> None:
        for exception in (KeyboardInterrupt, SystemExit, CancelledError):
            with self.subTest(exception=exception):
                provider = Mock(side_effect=exception())
                with self.assertRaises(exception):
                    make_analyzer(provider)(_context())

    def test_analyzer_factory_passes_current_repository_to_provider(self) -> None:
        received_repositories: list[RepositoryRef] = []

        def provider(repository: RepositoryRef):
            received_repositories.append(repository)
            return build_facts(None)

        result = make_analyzer(provider)(_context())

        self.assertEqual(result.status, DataStatus.UNAVAILABLE)
        self.assertEqual(received_repositories, [_context().repository])

    def test_factory_does_not_reuse_previous_repository_facts(self) -> None:
        first = _context()
        second = AnalysisContext(
            repository=RepositoryRef("other-id", "other-org", "other-repo"),
            commit_sha=first.commit_sha,
            analyzed_at=first.analyzed_at,
            period_start=first.period_start,
            period_end=first.period_end,
        )
        provider = Mock(side_effect=[build_facts([]), build_facts(None)])
        analyze = make_analyzer(provider)

        self.assertEqual(analyze(first).status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertEqual(analyze(second).status, DataStatus.UNAVAILABLE)
        self.assertEqual(
            [call.args[0] for call in provider.call_args_list],
            [first.repository, second.repository],
        )


def _context() -> AnalysisContext:
    analyzed_at = datetime(2026, 1, 1, tzinfo=UTC)
    return AnalysisContext(
        repository=RepositoryRef(
            id="repository-id-redacted",
            organization_slug="example-org",
            repository_slug="example-repo",
        ),
        commit_sha="commit-sha-redacted",
        analyzed_at=analyzed_at,
        period_start=analyzed_at - timedelta(days=90),
        period_end=analyzed_at,
    )


def _execution_with_security(security_result):
    return run_analysis(
        _context(),
        (
            AnalyzerRegistration("security", lambda _: security_result),
            *(
                AnalyzerRegistration(
                    category,
                    lambda _, category=category: CategoryResult(
                        category,
                        DataStatus.MEASURED,
                        90,
                        f"{category} measured",
                    ),
                )
                for category in ("cicd", "documentation", "activity", "issues", "code_health")
            ),
        ),
    )


def _complete_payload(*sast_groups: dict[str, object]) -> dict[str, object]:
    """Создаёт синтетический, но безопасный контракт полного AppSec-скана."""

    return {
        "engines": [
            _complete_engine("SAST", list(sast_groups)),
            _complete_engine("SCA", []),
            _complete_engine("SECRETS", []),
        ]
    }


def _complete_engine(engine: str, groups: list[dict[str, object]]) -> dict[str, object]:
    return {
        "engine": engine,
        "availability": "available",
        "finding_count": sum(group["count"] for group in groups),
        "severities": sorted({group["severity"] for group in groups}),
        "reason": None,
        "finding_groups": groups,
        "completeness": "complete",
    }
