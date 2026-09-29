from __future__ import annotations

import unittest
from datetime import UTC, datetime

import httpx
from fastapi import Request

from backend.app.analysis import (
    AnalysisJob,
    AnalysisJobStatus,
    AnalyzerRegistration,
    InMemoryAnalysisJobStore,
    InMemoryAnalysisStore,
    run_analysis,
)
from backend.app.analysis.dispatch import AnalysisPrincipal
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef
from backend.app.integrations.sourcecraft_repositories import SourceCraftRepository
from backend.app.main import create_app
from backend.app.scheduling.runner import SYSTEM_SCHEDULER_SUBJECT

OWNER_TOKEN = "report-owner-token"
OTHER_TOKEN = "report-other-token"
OWNER_ID = "user-owner"
OTHER_ID = "user-other"
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
                owner_subject=OWNER_ID,
            )
        )
        await self.job_store.mark_running("analysis-42", timestamp)
        await self.job_store.finish(
            "analysis-42",
            status=AnalysisJobStatus.PARTIAL,
            finished_at=timestamp,
        )
        await self.store.save("analysis-public-42", self.execution)
        await self.job_store.create(
            AnalysisJob.queued(
                analysis_id="analysis-public-42",
                repository_id="repo-42",
                created_at=timestamp,
                owner_subject=SYSTEM_SCHEDULER_SUBJECT,
            )
        )
        await self.job_store.mark_running("analysis-public-42", timestamp)
        await self.job_store.finish(
            "analysis-public-42",
            status=AnalysisJobStatus.PARTIAL,
            finished_at=timestamp,
        )
        self.headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        self.catalog = _Catalog((_public_repository(),))
        self.app = create_app(
            analysis_store=self.store,
            job_store=self.job_store,
            principal_provider=_session_principal,
            repository_catalog=self.catalog,
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
        self.assertTrue(response.json()["badgeAvailable"])

    async def test_disables_badge_when_repository_is_not_public(self) -> None:
        self.catalog.repositories = ()

        async with api_client(self.app) as client:
            response = await client.get(
                "/api/v1/analyses/analysis-42/report",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["badgeAvailable"])

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
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json(), {"detail": "Analysis not found."})
                    self._assert_hides_private_report(response)

    async def test_public_scheduled_snapshot_opens_without_a_session(self) -> None:
        async with api_client(self.app) as client:
            status = await client.get("/api/v1/analyses/analysis-public-42")
            report = await client.get("/api/v1/analyses/analysis-public-42/report")
            markdown = await client.get("/api/v1/analyses/analysis-public-42/report.md")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["id"], "analysis-public-42")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()["analysis"]["id"], "analysis-public-42")
        self.assertTrue(report.json()["badgeAvailable"])
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("analysis-public-42", markdown.text)

    async def test_public_scheduled_snapshot_is_hidden_after_catalog_removal(self) -> None:
        self.catalog.repositories = ()

        async with api_client(self.app) as client:
            for path in (
                "/api/v1/analyses/analysis-public-42",
                "/api/v1/analyses/analysis-public-42/report",
                "/api/v1/analyses/analysis-public-42/report.md",
            ):
                with self.subTest(path=path):
                    response = await client.get(path)

                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json(), {"detail": "Authentication required."})

    def _assert_hides_private_report(self, response: httpx.Response) -> None:
        rendered = response.text
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, rendered)
        self.assertNotIn(OWNER_TOKEN, rendered)


async def _session_principal(request: Request) -> AnalysisPrincipal:
    header = request.headers.get("authorization") or ""
    token = header.removeprefix("Bearer ").strip()
    if token == OWNER_TOKEN:
        return AnalysisPrincipal(OWNER_ID)
    if token == OTHER_TOKEN:
        return AnalysisPrincipal(OTHER_ID)
    raise PermissionError("authentication required")


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


class _Catalog:
    def __init__(self, repositories: tuple[SourceCraftRepository, ...]) -> None:
        self.repositories = repositories

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        return self.repositories


def _public_repository() -> SourceCraftRepository:
    return SourceCraftRepository(
        id="repo-42",
        name="platform-api",
        organization_slug="team",
        slug="platform-api",
        default_branch="main",
        visibility="public",
        is_empty=False,
        language=None,
        branch_count=1,
        web_url=None,
    )
