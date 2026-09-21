from __future__ import annotations

import asyncio
import threading
import unittest
from datetime import UTC, datetime

import httpx

from backend.app.analysis import (
    AnalysisExecutionService,
    AnalysisPrincipal,
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
        self.context_resolver = ContextResolver(self.timestamp)
        self.dispatcher = InProcessAnalysisDispatcher(
            execution_service=self.execution_service,
            context_resolver=self.context_resolver,
            analyzer_provider=lambda _: (activity_registration(),),
            analysis_id_factory=lambda: "analysis-42",
        )
        self.app = create_app(
            analysis_store=self.snapshot_store,
            job_store=self.job_store,
            analysis_dispatcher=self.dispatcher,
            principal_provider=authenticated_principal,
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
        self.assertEqual(
            self.context_resolver.principals,
            [AnalysisPrincipal("user-42")],
        )

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

    async def test_serves_running_status_while_sync_analyzer_waits_in_thread(self) -> None:
        analyzer_started = threading.Event()
        allow_analyzer_to_finish = threading.Event()
        dispatcher = InProcessAnalysisDispatcher(
            execution_service=self.execution_service,
            context_resolver=ContextResolver(self.timestamp),
            analyzer_provider=lambda _: (
                blocking_activity_registration(
                    analyzer_started,
                    allow_analyzer_to_finish,
                ),
            ),
            analysis_id_factory=lambda: "analysis-42",
        )
        app = create_app(
            analysis_store=self.snapshot_store,
            job_store=self.job_store,
            analysis_dispatcher=dispatcher,
            principal_provider=authenticated_principal,
        )

        try:
            async with api_client(app) as client:
                created = await client.post("/api/v1/repositories/repo-42/analyses")
                await _wait_for_thread_event(analyzer_started)
                running = await asyncio.wait_for(
                    client.get("/api/v1/analyses/analysis-42"),
                    timeout=0.2,
                )
                allow_analyzer_to_finish.set()
                completed = await _wait_for_terminal_status(client, "analysis-42")
        finally:
            allow_analyzer_to_finish.set()
            await dispatcher.close()

        self.assertEqual(created.status_code, 202)
        self.assertEqual(running.status_code, 200)
        self.assertEqual(running.json()["status"], "running")
        self.assertEqual(completed.json()["status"], "partial")

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

    async def test_rejects_analysis_when_authentication_is_not_configured(self) -> None:
        app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            job_store=InMemoryAnalysisJobStore(),
            analysis_dispatcher=self.dispatcher,
        )

        async with api_client(app) as client:
            response = await client.post("/api/v1/repositories/repo-42/analyses")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Analysis authentication is not configured."},
        )

    async def test_returns_forbidden_when_resolver_denies_repository_access(self) -> None:
        dispatcher = InProcessAnalysisDispatcher(
            execution_service=self.execution_service,
            context_resolver=DenyingContextResolver(),
            analyzer_provider=lambda _: (),
            analysis_id_factory=lambda: "analysis-42",
        )
        app = create_app(
            analysis_store=self.snapshot_store,
            job_store=self.job_store,
            analysis_dispatcher=dispatcher,
            principal_provider=authenticated_principal,
        )

        try:
            async with api_client(app) as client:
                response = await client.post("/api/v1/repositories/repo-42/analyses")
        finally:
            await dispatcher.close()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Repository access denied."})


class ContextResolver:
    def __init__(self, timestamp: datetime) -> None:
        self._timestamp = timestamp
        self.principals: list[AnalysisPrincipal] = []

    async def resolve(
        self,
        repository_id: str,
        principal: AnalysisPrincipal,
    ) -> AnalysisContext:
        self.principals.append(principal)
        return AnalysisContext(
            repository=RepositoryRef(repository_id, "team", "platform-api"),
            commit_sha="abc123",
            analyzed_at=self._timestamp,
            period_start=self._timestamp,
            period_end=self._timestamp,
        )


class DenyingContextResolver:
    async def resolve(
        self,
        _: str,
        __: AnalysisPrincipal,
    ) -> AnalysisContext:
        raise PermissionError("repository access denied")


async def authenticated_principal(_: httpx.Request) -> AnalysisPrincipal:
    return AnalysisPrincipal("user-42")


async def _wait_for_terminal_status(
    client: httpx.AsyncClient,
    analysis_id: str,
) -> httpx.Response:
    for _ in range(100):
        response = await client.get(f"/api/v1/analyses/{analysis_id}")
        if response.json()["status"] in {"completed", "partial", "failed"}:
            return response
        await asyncio.sleep(0)

    raise AssertionError("Background analysis did not finish.")


async def _wait_for_thread_event(event: threading.Event) -> None:
    for _ in range(100):
        if event.is_set():
            return
        await asyncio.sleep(0.01)

    raise AssertionError("Synchronous analyzer did not start.")


def activity_registration() -> AnalyzerRegistration:
    def evaluate(_: AnalysisContext) -> CategoryResult:
        return CategoryResult(
            category="activity",
            status=DataStatus.MEASURED,
            score=80,
            summary="Активность измерена.",
        )

    return AnalyzerRegistration("activity", evaluate)


def blocking_activity_registration(
    started: threading.Event,
    release: threading.Event,
) -> AnalyzerRegistration:
    def evaluate(_: AnalysisContext) -> CategoryResult:
        started.set()
        if not release.wait(timeout=2):
            raise RuntimeError("Test analyzer did not receive release signal.")
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
