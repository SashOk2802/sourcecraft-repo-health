"""Сквозная проверка Security: runner → хранилище → JSON/Markdown HTTP-отчёты.

Все маркеры и непустые AppSec-объекты здесь синтетические: они проверяют
отсутствие утечек, а не документируют ещё не подтверждённую схему SourceCraft.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import httpx
from fastapi import Request

from backend.app.analysis import (
    AnalysisJob,
    AnalysisPrincipal,
    AnalyzerRegistration,
    InMemoryAnalysisJobStore,
    InMemoryAnalysisStore,
    run_analysis,
)
from backend.app.analyzers.security import build_facts, make_analyzer
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef
from backend.app.main import create_app


class SecurityReportingTest(unittest.IsolatedAsyncioTestCase):
    async def test_security_states_survive_storage_and_both_http_report_formats(self) -> None:
        fixture = Path(__file__).parent / "fixtures/sourcecraft/appsec_defects_null.json"
        null_payload = json.loads(fixture.read_text(encoding="utf-8"))
        cases = (
            (build_facts(null_payload), "unavailable", "appsec_unavailable", "unavailable"),
            (build_facts([]), "insufficient_sample", "security_scoring_not_configured", "received"),
            (
                build_facts({"defects": [{"snippet": "synthetic-finding-marker"}]}),
                "insufficient_sample",
                "security_scoring_not_configured",
                "received",
            ),
            (
                build_facts(None, source_error="Bearer synthetic-token-marker"),
                "error",
                "appsec_source_error",
                "error",
            ),
        )
        for facts, status, reason, availability in cases:
            with self.subTest(status=status, availability=availability):
                provider = Mock(return_value=facts)
                report, markdown = await self._reports(provider)
                self._assert_security_report(report, markdown, status, reason, availability)
                provider.assert_called_once_with(_context().repository)

    async def test_provider_exception_cannot_leak_through_runner_logs_or_reports(self) -> None:
        provider = Mock(side_effect=RuntimeError("Bearer synthetic-token-marker"))

        with self.assertLogs("backend.app", level="WARNING") as logs:
            report, markdown = await self._reports(provider)

        self.assertNotIn("synthetic-token-marker", "\n".join(logs.output))
        self.assertTrue(all(record.exc_info is None for record in logs.records))
        self._assert_security_report(report, markdown, "error", "appsec_source_error", "error")

    def test_unmeasured_security_alone_does_not_create_an_overall_score(self) -> None:
        provider = Mock(return_value=build_facts([]))
        execution = run_analysis(
            _context(), (AnalyzerRegistration("security", make_analyzer(provider)),)
        )

        self.assertIsNone(execution.analysis.score)
        self.assertEqual(execution.score_summary.coverage, 0)
        self.assertEqual(execution.score_summary.measured_weight, 0)
        self.assertTrue(execution.score_summary.is_preliminary)

    async def _reports(self, provider) -> tuple[dict, str]:
        execution = run_analysis(
            _context(),
            (
                AnalyzerRegistration("security", make_analyzer(provider)),
                AnalyzerRegistration(
                    "activity",
                    lambda _: CategoryResult("activity", DataStatus.MEASURED, 80, "Activity"),
                ),
            ),
        )
        store = InMemoryAnalysisStore()
        job_store = InMemoryAnalysisJobStore()
        await job_store.create(
            AnalysisJob.queued(
                analysis_id="security-test",
                repository_id=_context().repository.id,
                created_at=_context().analyzed_at,
                owner_subject="security-report-owner",
            )
        )
        app = create_app(
            analysis_store=store,
            job_store=job_store,
            principal_provider=_security_report_principal,
        )
        async with app.router.lifespan_context(app):
            await store.save("security-test", execution)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                json_response = await client.get("/api/v1/analyses/security-test/report")
                markdown_response = await client.get("/api/v1/analyses/security-test/report.md")

        self.assertEqual(json_response.status_code, 200)
        self.assertEqual(markdown_response.status_code, 200)
        self.assertEqual(json_response.headers["content-type"], "application/json")
        self.assertTrue(markdown_response.headers["content-type"].startswith("text/markdown"))
        return json_response.json(), markdown_response.text

    def _assert_security_report(self, report, markdown, status, reason, availability) -> None:
        security = next(item for item in report["categories"] if item["code"] == "security")
        self.assertEqual(security["status"], status)
        self.assertEqual(security["reason"], reason)
        self.assertIsNone(security["score"])
        self.assertIsNone(security["effectiveWeight"])
        self.assertIsNone(security["points"])
        self.assertEqual(security["evidence"][0]["code"], "appsec_data_availability")
        self.assertEqual(security["evidence"][0]["value"], availability)
        self.assertIsNone(security["evidence"][0]["normalizedScore"])
        self.assertEqual(report["score"], 80)
        self.assertEqual(report["analysis"]["coverage"], 0.15)
        self.assertEqual(report["analysis"]["status"], "partial")
        self.assertIsNone(report["analysis"]["scoreLimit"])
        self.assertEqual(report["recommendations"], [])
        self.assertIn(reason, markdown)
        for marker in ("synthetic-token-marker", "synthetic-finding-marker"):
            self.assertNotIn(marker, json.dumps(report))
            self.assertNotIn(marker, markdown)


def _context() -> AnalysisContext:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    return AnalysisContext(
        repository=RepositoryRef("example-id", "example-org", "example-repo"),
        commit_sha="example-commit",
        analyzed_at=timestamp,
        period_start=timestamp,
        period_end=timestamp,
    )


async def _security_report_principal(_: Request) -> AnalysisPrincipal:
    """Представляет владельца сохранённого отчёта без токена в тесте."""

    return AnalysisPrincipal("security-report-owner")
