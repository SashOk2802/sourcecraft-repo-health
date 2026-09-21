from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime

import httpx

from backend.app.analysis import (
    AnalysisExecutionService,
    AnalyzerRegistration,
    InMemoryAnalysisJobStore,
    InMemoryAnalysisStore,
    InProcessAnalysisDispatcher,
)
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef
from backend.app.main import create_app


class AnalysisApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.timestamp = datetime(2026, 9, 21, 12, tzinfo=UTC)
        self.snapshot_store = InMemoryAnalysisStore()
        self.job_store = InMemoryAnalysisJobStore()
        self.execution_service = AnalysisExecutionService(
            job_store=self.job_store,
            snapshot_store=self.snapshot_store,
        )
        self.dispatcher = InProcessAnalysisDispatcher(
            execution_service=self.execution_service,
            context_resolver=ContextResolver(self.timestamp),
            analyzer_provider=lambda _: (activity_registration(),),
            analysis_id_factory=lambda: "analysis-42",
        )
        self.app = create_app(
            analysis_store=self.snapshot_store,
            job_store=self.job_store,
            analysis_dispatcher=self.dispatcher,
        )

    async def asyncTearDown(self) -> None:
        await self.dispatcher.close()

    async def test_creates_job_runs_worker_and_exposes_terminal_report(self) -> None:
        async with api_client(self.app) as client:
            created = await client.post("/api/v1/repositories/repo-42/analyses")
            completed = await _wait_for_terminal_status(client, "analysis-42")
            report = await client.get("/api/v1/analyses/analysis-42/report")

        self.assertEqual(created.status_code, 202)
        self.assertEqual(created.json()["id"], "analysis-42")
        self.assertEqual(created.json()["status"], "queued")
        self.assertIsNone(created.json()["reportUrl"])

        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "partial")
        self.assertEqual(completed.json()["score"], 80)
        self.assertTrue(completed.json()["isPreliminary"])
        self.assertEqual(
            completed.json()["reportUrl"],
            "/api/v1/analyses/analysis-42/report",
        )
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()["analysis"]["id"], "analysis-42")

    async def test_returns_service_unavailable_without_sourcecraft_dispatcher(self) -> None:
        app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            job_store=InMemoryAnalysisJobStore(),
        )

        async with api_client(app) as client:
            response = await client.post("/api/v1/repositories/repo-42/analyses")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Analysis dispatch is not configured."},
        )


class ContextResolver:
    def __init__(self, timestamp: datetime) -> None:
        self._timestamp = timestamp

    async def resolve(self, repository_id: str) -> AnalysisContext:
        return AnalysisContext(
            repository=RepositoryRef(repository_id, "team", "platform-api"),
            commit_sha="abc123",
            analyzed_at=self._timestamp,
            period_start=self._timestamp,
            period_end=self._timestamp,
        )


async def _wait_for_terminal_status(
    client: httpx.AsyncClient,
    analysis_id: str,
) -> httpx.Response:
    for _ in range(20):
        response = await client.get(f"/api/v1/analyses/{analysis_id}")
        if response.json()["status"] in {"completed", "partial", "failed"}:
            return response
        await asyncio.sleep(0)

    raise AssertionError("Background analysis did not finish.")


def activity_registration() -> AnalyzerRegistration:
    def evaluate(_: AnalysisContext) -> CategoryResult:
        return CategoryResult(
            category="activity",
            status=DataStatus.MEASURED,
            score=80,
            summary="Активность измерена.",
        )

    return AnalyzerRegistration("activity", evaluate)


def api_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )
