from __future__ import annotations

import unittest
from datetime import UTC, datetime

import httpx

from backend.app.analysis import AnalyzerRegistration, InMemoryAnalysisStore, run_analysis
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef
from backend.app.main import create_app


class ReportApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        timestamp = datetime(2026, 9, 18, 15, 30, tzinfo=UTC)
        context = AnalysisContext(
            repository=RepositoryRef("repo-42", "team", "platform-api"),
            commit_sha="abc123",
            analyzed_at=timestamp,
            period_start=timestamp,
            period_end=timestamp,
        )
        self.execution = run_analysis(context, (registration("activity", 80),))
        self.store = InMemoryAnalysisStore()
        self.store.save("analysis-42", self.execution)
        self.app = create_app(analysis_store=self.store)

    async def test_returns_json_report_for_saved_analysis(self) -> None:
        async with api_client(self.app) as client:
            response = await client.get("/api/v1/analyses/analysis-42/report")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(response.json()["analysis"]["id"], "analysis-42")
        self.assertEqual(response.json()["analysis"]["status"], "partial")
        self.assertEqual(response.json()["score"], 80)

    async def test_returns_markdown_from_the_same_snapshot(self) -> None:
        async with api_client(self.app) as client:
            response = await client.get("/api/v1/analyses/analysis-42/report.md")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/markdown"))
        self.assertIn("Анализ `analysis-42`", response.text)
        self.assertIn("Предварительная оценка: да.", response.text)

    async def test_unknown_analysis_returns_not_found(self) -> None:
        async with api_client(self.app) as client:
            for path in (
                "/api/v1/analyses/missing/report",
                "/api/v1/analyses/missing/report.md",
            ):
                with self.subTest(path=path):
                    response = await client.get(path)

                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json(), {"detail": "Analysis not found."})


def registration(category: str, score: float) -> AnalyzerRegistration:
    def evaluate(_: AnalysisContext) -> CategoryResult:
        return CategoryResult(
            category=category,
            status=DataStatus.MEASURED,
            score=score,
            summary=f"Результат категории {category}.",
        )

    return AnalyzerRegistration(category, evaluate)


def api_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )
