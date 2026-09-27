"""Проверяет безопасную адаптацию AppSec-сводки к SecurityFacts."""

from __future__ import annotations

import unittest
from asyncio import CancelledError
from datetime import UTC, datetime
from unittest.mock import Mock

from backend.app.analyzers.security import make_analyzer
from backend.app.contracts import AnalysisContext, DataStatus, RepositoryRef
from backend.app.integrations.sourcecraft_appsec_facts import (
    collect_security_facts,
    make_security_facts_provider,
)
from backend.app.integrations.sourcecraft_appsec_probe import AppSecProbeResult


class SourceCraftAppSecFactsTest(unittest.TestCase):
    def test_available_engine_becomes_safe_insufficient_sample(self) -> None:
        probe = Mock()
        probe.probe_all.return_value = (
            AppSecProbeResult("SECRETS", "error", None, reason="sourcecraft_cli_timeout"),
            AppSecProbeResult(
                "SAST",
                "available",
                3,
                severities=("CRITICAL", "HIGH"),
                completeness="unknown",
            ),
            AppSecProbeResult(
                "SCA",
                "unavailable",
                None,
                reason="sourcecraft_appsec_unavailable",
            ),
        )

        facts = collect_security_facts(probe, _repository())
        result = make_analyzer(lambda _: facts)(_context())

        self.assertIsNone(facts.source_error)
        self.assertEqual(
            facts.payload,
            {
                "engines": [
                    {
                        "engine": "SAST",
                        "availability": "available",
                        "finding_count": 3,
                        "severities": ["CRITICAL", "HIGH"],
                        "reason": None,
                        "finding_groups": None,
                        "completeness": "unknown",
                    },
                    {
                        "engine": "SCA",
                        "availability": "unavailable",
                        "finding_count": None,
                        "severities": [],
                        "reason": "sourcecraft_appsec_unavailable",
                        "finding_groups": None,
                        "completeness": None,
                    },
                    {
                        "engine": "SECRETS",
                        "availability": "error",
                        "finding_count": None,
                        "severities": [],
                        "reason": "sourcecraft_cli_timeout",
                        "finding_groups": None,
                        "completeness": None,
                    },
                ]
            },
        )
        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        self.assertEqual(probe.probe_all.call_args.args, ("example-org/example-repo",))

    def test_all_unavailable_is_not_reported_as_zero_findings(self) -> None:
        probe = Mock()
        probe.probe_all.return_value = tuple(
            AppSecProbeResult(
                engine,
                "unavailable",
                None,
                reason="sourcecraft_appsec_unavailable",
            )
            for engine in ("SAST", "SCA", "SECRETS")
        )

        facts = collect_security_facts(probe, _repository())
        result = make_analyzer(lambda _: facts)(_context())

        self.assertIsNone(facts.payload)
        self.assertIsNone(facts.source_error)
        self.assertEqual(result.status, DataStatus.UNAVAILABLE)
        self.assertIsNone(result.score)

    def test_error_without_available_data_becomes_safe_source_error(self) -> None:
        probe = Mock()
        probe.probe_all.return_value = (
            AppSecProbeResult("SAST", "error", None, reason="sourcecraft_cli_error"),
            AppSecProbeResult(
                "SCA", "unavailable", None, reason="sourcecraft_appsec_unavailable"
            ),
            AppSecProbeResult(
                "SECRETS", "unavailable", None, reason="sourcecraft_appsec_unavailable"
            ),
        )

        facts = collect_security_facts(probe, _repository())
        result = make_analyzer(lambda _: facts)(_context())

        self.assertEqual(facts.source_error, "sourcecraft_appsec_probe_failed")
        self.assertNotIn("sourcecraft_cli_error", repr(facts))
        self.assertEqual(result.status, DataStatus.ERROR)
        self.assertIsNone(result.score)

    def test_invalid_or_incomplete_probe_result_is_mapping_error(self) -> None:
        valid_sast = AppSecProbeResult("SAST", "available", 0, completeness="unknown")
        cases = (
            (),
            (valid_sast,),
            (valid_sast, valid_sast, valid_sast),
            (valid_sast, object(), object()),
        )
        for probe_results in cases:
            with self.subTest(probe_results=probe_results):
                probe = Mock()
                probe.probe_all.return_value = probe_results

                facts = collect_security_facts(probe, _repository())

                self.assertIsNone(facts.payload)
                self.assertEqual(facts.source_error, "sourcecraft_appsec_mapping_failed")

    def test_probe_exception_is_redacted_and_provider_feeds_analyzer(self) -> None:
        probe = Mock()
        probe.probe_all.side_effect = RuntimeError("private-repo synthetic-secret-marker")
        provider = make_security_facts_provider(probe)

        facts = provider(_repository())
        result = make_analyzer(provider)(_context())

        self.assertEqual(facts.source_error, "sourcecraft_appsec_probe_failed")
        self.assertNotIn("synthetic-secret-marker", repr(facts))
        self.assertEqual(result.status, DataStatus.ERROR)

    def test_process_interruptions_are_not_hidden_as_probe_errors(self) -> None:
        for interruption in (CancelledError(), KeyboardInterrupt()):
            with self.subTest(interruption=type(interruption).__name__):
                probe = Mock()
                probe.probe_all.side_effect = interruption

                with self.assertRaises(type(interruption)):
                    collect_security_facts(probe, _repository())


def _repository() -> RepositoryRef:
    return RepositoryRef("repository-id-redacted", "example-org", "example-repo")


def _context() -> AnalysisContext:
    timestamp = datetime(2026, 2, 1, tzinfo=UTC)
    return AnalysisContext(
        repository=_repository(),
        commit_sha="commit-sha-redacted",
        analyzed_at=timestamp,
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=timestamp,
    )
