from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime

import httpx

from backend.app.analysis import (
    AnalysisJob,
    AnalysisJobStatus,
    AnalyzerRegistration,
    InMemoryAnalysisJobStore,
    InMemoryAnalysisStore,
    run_analysis,
)
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef
from backend.app.launch import principal_from_authorization
from backend.app.main import create_app

OWNER_TOKEN = "report-owner-token"
OTHER_TOKEN = "report-other-token"
PRIVATE_MARKERS = ("platform-api", "abc123", "team")


class ReportApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
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
        self.job_store = InMemoryAnalysisJobStore()
        await self.store.save("analysis-42", self.execution)
        await self.job_store.create(
            AnalysisJob.queued(
                analysis_id="analysis-42",
                repository_id="repo-42",
                created_at=timestamp,
                owner_subject=_subject(OWNER_TOKEN),
            )
        )
        await self.job_store.mark_running("analysis-42", timestamp)
        await self.job_store.finish(
            "analysis-42",
            status=AnalysisJobStatus.PARTIAL,
            finished_at=timestamp,
        )
        self.headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        self.app = create_app(
            analysis_store=self.store,
            job_store=self.job_store,
            principal_provider=principal_from_authorization,
        )

    async def test_returns_status_for_saved_analysis(self) -> None:
        async with api_client(self.app) as client:
            response = await client.get("/api/v1/analyses/analysis-42", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": "analysis-42",
                "status": "partial",
                "repository": {
                    "id": "repo-42",
                    "organizationSlug": "team",
                    "repositorySlug": "platform-api",
                    "name": "team/platform-api",
                    "url": None,
                },
                "score": 80,
                "isPreliminary": True,
                "createdAt": "2026-09-18T15:30:00Z",
                "startedAt": "2026-09-18T15:30:00Z",
                "finishedAt": "2026-09-18T15:30:00Z",
                "error": None,
                "reportUrl": "/api/v1/analyses/analysis-42/report",
                "markdownReportUrl": "/api/v1/analyses/analysis-42/report.md",
            },
        )

    async def test_rejects_unsafe_identifier_in_status_endpoint(self) -> None:
        async with api_client(self.app) as client:
            response = await client.get("/api/v1/analyses/analysis%3Fretry%3D1")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Analysis not found."})

    async def test_returns_json_report_for_saved_analysis(self) -> None:
        async with api_client(self.app) as client:
            response = await client.get(
                "/api/v1/analyses/analysis-42/report",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(response.json()["analysis"]["id"], "analysis-42")
        self.assertEqual(response.json()["analysis"]["status"], "partial")
        self.assertEqual(response.json()["score"], 80)

    async def test_returns_markdown_from_the_same_snapshot(self) -> None:
        async with api_client(self.app) as client:
            response = await client.get(
                "/api/v1/analyses/analysis-42/report.md",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/markdown"))
        self.assertIn("Анализ " + chr(96) + "analysis-42" + chr(96), response.text)
        self.assertIn("Предварительная оценка: да.", response.text)

    async def test_normalizes_identifier_before_lookup_and_rendering(self) -> None:
        async with api_client(self.app) as client:
            json_response = await client.get(
                "/api/v1/analyses/analysis-42%20/report",
                headers=self.headers,
            )
            markdown_response = await client.get(
                "/api/v1/analyses/analysis-42%20/report.md",
                headers=self.headers,
            )

        self.assertEqual(json_response.status_code, 200)
        self.assertEqual(json_response.json()["analysis"]["id"], "analysis-42")
        self.assertEqual(markdown_response.status_code, 200)
        self.assertIn(
            "Анализ " + chr(96) + "analysis-42" + chr(96),
            markdown_response.text,
        )
        self.assertNotIn(
            "Анализ " + chr(96) + "analysis-42 " + chr(96),
            markdown_response.text,
        )

    async def test_unknown_analysis_returns_not_found(self) -> None:
        async with api_client(self.app) as client:
            for path in (
                "/api/v1/analyses/missing/report",
                "/api/v1/analyses/missing/report.md",
            ):
                with self.subTest(path=path):
                    response = await client.get(path, headers=self.headers)

                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json(), {"detail": "Analysis not found."})

    async def test_private_report_requires_owner(self) -> None:
        paths = (
            "/api/v1/analyses/analysis-42",
            "/api/v1/analyses/analysis-42/report",
            "/api/v1/analyses/analysis-42/report.md",
        )
        async with api_client(self.app) as client:
            for path in paths:
                with self.subTest(path=path, case="missing-token"):
                    response = await client.get(path)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json(), {"detail": "Authentication required."})
                    self._assert_hides_private_report(response)

                with self.subTest(path=path, case="other-user"):
                    response = await client.get(
                        path,
                        headers={"Authorization": f"Bearer {OTHER_TOKEN}"},
                    )
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json(), {"detail": "Analysis access denied."})
                    self._assert_hides_private_report(response)

    def _assert_hides_private_report(self, response: httpx.Response) -> None:
        rendered = response.text
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, rendered)
        self.assertNotIn(OWNER_TOKEN, rendered)


def _subject(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"token:{digest}"


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
