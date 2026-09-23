"""Проверяет честную обработку недоступных и неподготовленных AppSec-данных."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.analyzers.security import (
    CATEGORY_CODE,
    build_facts,
    evaluate,
    make_analyzer,
)
from backend.app.contracts import AnalysisContext, DataStatus, RepositoryRef


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

    def test_received_payload_is_insufficient_until_scoring_methodology_exists(self) -> None:
        for payload in ([], {"defects": []}):
            with self.subTest(payload=payload):
                result = evaluate(build_facts(payload))

                self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
                self.assertIsNone(result.score)
                self.assertEqual(result.reason, "security_scoring_not_configured")

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

    def test_analyzer_factory_passes_current_repository_to_provider(self) -> None:
        received_repositories: list[RepositoryRef] = []

        def provider(repository: RepositoryRef):
            received_repositories.append(repository)
            return build_facts(None)

        result = make_analyzer(provider)(_context())

        self.assertEqual(result.status, DataStatus.UNAVAILABLE)
        self.assertEqual(received_repositories, [_context().repository])


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
