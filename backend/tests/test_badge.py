"""Тестирует генерацию SVG-бейджей и HTTP-эндпоинты для README."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from unittest import mock

import httpx

from backend.app.analysis import run_analysis
from backend.app.analysis.runner import AnalysisExecution
from backend.app.analysis.store import InMemoryAnalysisStore
from backend.app.contracts import AnalysisContext, RepositoryRef
from backend.app.integrations.sourcecraft_repositories import SourceCraftRepository
from backend.app.main import create_app
from backend.app.reporting.badge import _estimate_text_width, render_score_badge


def _sample_execution(
    *,
    repository_id: str = "repo-42",
    organization_slug: str = "test-org",
    repository_slug: str = "test-repo",
    score: float | None = 85,
    is_preliminary: bool = False,
) -> AnalysisExecution:
    analyzed_at = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    context = AnalysisContext(
        repository=RepositoryRef(repository_id, organization_slug, repository_slug),
        commit_sha="0123456789abcdef",
        analyzed_at=analyzed_at,
        period_start=analyzed_at,
        period_end=analyzed_at,
    )
    execution = run_analysis(context, ())
    return replace(
        execution,
        analysis=replace(execution.analysis, score=score),
        score_summary=replace(execution.score_summary, is_preliminary=is_preliminary),
    )


class ScoreBadgeTest(unittest.TestCase):
    def test_high_score_badge_is_green(self) -> None:
        svg = render_score_badge(85)
        self.assertIn("#2ea44f", svg)
        self.assertIn("85/100", svg)
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)
        self.assertIn('aria-label="repo health: 85/100"', svg)

    def test_mid_score_badge_is_amber(self) -> None:
        svg = render_score_badge(65)
        self.assertIn("#dfb317", svg)
        self.assertIn("65/100", svg)

    def test_low_score_badge_is_red(self) -> None:
        svg = render_score_badge(45)
        self.assertIn("#cb2431", svg)
        self.assertIn("45/100", svg)

    def test_preliminary_badge_is_blue(self) -> None:
        svg = render_score_badge(None, is_preliminary=True)
        self.assertIn("#0969da", svg)
        self.assertIn("preliminary", svg)

    def test_preliminary_takes_precedence_over_score(self) -> None:
        svg = render_score_badge(85, is_preliminary=True)
        self.assertIn("#0969da", svg)
        self.assertIn("preliminary", svg)
        self.assertNotIn("85/100", svg)

    def test_estimate_text_width_with_unicode(self) -> None:
        ascii_width = _estimate_text_width("status")
        cyrillic_width = _estimate_text_width("статус")
        self.assertGreater(cyrillic_width, ascii_width)

    def test_no_data_badge_is_gray(self) -> None:
        svg = render_score_badge(None, is_preliminary=False)
        self.assertIn("#6a737d", svg)
        self.assertIn("no data", svg)

    def test_xml_escaping(self) -> None:
        svg = render_score_badge(label="foo & <bar>", value_override="test & 'quote'")
        self.assertIn("foo &amp; &lt;bar&gt;", svg)
        self.assertIn("test &amp; 'quote'", svg)


class _Catalog:
    def __init__(self) -> None:
        self.repositories: tuple[SourceCraftRepository, ...] = ()

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        return self.repositories


def _public_repository(
    repository_id: str,
    organization_slug: str,
    repository_slug: str,
) -> SourceCraftRepository:
    return SourceCraftRepository(
        id=repository_id,
        name=repository_slug,
        organization_slug=organization_slug,
        slug=repository_slug,
        default_branch="main",
        visibility="public",
        is_empty=False,
        language=None,
        branch_count=1,
        web_url=None,
    )


class BadgeApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = InMemoryAnalysisStore()
        self.catalog = _Catalog()
        await self.store.start()
        self.app = create_app(analysis_store=self.store, repository_catalog=self.catalog)

    async def asyncTearDown(self) -> None:
        await self.store.close()

    async def test_analysis_badge_returns_svg_with_cors_headers(self) -> None:
        execution = _sample_execution(score=92)
        await self.store.save("analysis-1", execution)
        self.catalog.repositories = (_public_repository("repo-42", "test-org", "test-repo"),)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/analyses/analysis-1/badge.svg")

        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response.headers.get("content-type", ""))
        self.assertEqual(response.headers.get("access-control-allow-origin"), "*")
        self.assertEqual(response.headers.get("cross-origin-resource-policy"), "cross-origin")
        self.assertIn("public, max-age=300", response.headers.get("cache-control", ""))
        self.assertIn("92/100", response.text)
        self.assertIn("#2ea44f", response.text)

    async def test_missing_analysis_badge_returns_unknown_svg(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/analyses/unknown-id/badge.svg")

        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response.headers.get("content-type", ""))
        self.assertIn("unknown", response.text)
        self.assertIn("#6a737d", response.text)

    async def test_repository_badge_returns_latest_score_svg(self) -> None:
        execution = _sample_execution(
            repository_id="repo-42",
            organization_slug="my-org",
            repository_slug="my-repo",
            score=73,
        )
        await self.store.save("analysis-1", execution)
        self.catalog.repositories = (_public_repository("repo-42", "my-org", "my-repo"),)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/repositories/my-org/my-repo/badge.svg")

        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response.headers.get("content-type", ""))
        self.assertEqual(response.headers.get("access-control-allow-origin"), "*")
        self.assertEqual(response.headers.get("cross-origin-resource-policy"), "cross-origin")
        self.assertIn("73/100", response.text)
        self.assertIn("#dfb317", response.text)

    async def test_private_repository_badge_is_neutral(self) -> None:
        execution = _sample_execution(score=92)
        await self.store.save("analysis-1", execution)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        ) as client:
            by_analysis = await client.get("/api/v1/analyses/analysis-1/badge.svg")
            by_repository = await client.get("/api/v1/repositories/test-org/test-repo/badge.svg")

        self.assertIn("unknown", by_analysis.text)
        self.assertIn("unknown", by_repository.text)
        self.assertNotIn("92/100", by_analysis.text)
        self.assertNotIn("92/100", by_repository.text)

    async def test_badge_rounds_score_like_frontend(self) -> None:
        execution = _sample_execution(score=75.5)
        await self.store.save("analysis-1", execution)
        self.catalog.repositories = (_public_repository("repo-42", "test-org", "test-repo"),)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/analyses/analysis-1/badge.svg")

        self.assertIn("76/100", response.text)
        self.assertNotIn("75/100", response.text)

    async def test_missing_repository_badge_returns_unknown_svg(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/repositories/missing-org/missing-repo/badge.svg")

        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response.headers.get("content-type", ""))
        self.assertIn("unknown", response.text)
        self.assertIn("#6a737d", response.text)

    async def test_badge_cache_control_not_overwritten_by_sensitive_prefixes(self) -> None:
        execution = _sample_execution(score=92)
        await self.store.save("analysis-1", execution)
        self.catalog.repositories = (_public_repository("repo-42", "test-org", "test-repo"),)

        sensitive_with_badges = (
            "/api/v1/auth/",
            "/api/v1/me",
            "/api/v1/connections/",
            "/api/v1/repositories/",
            "/api/v1/analyses/",
        )
        with mock.patch("backend.app.main._SENSITIVE_RESPONSE_PREFIXES", sensitive_with_badges):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
            ) as client:
                response = await client.get("/api/v1/analyses/analysis-1/badge.svg")

            self.assertEqual(response.status_code, 200)
            self.assertIn("public, max-age=300", response.headers.get("cache-control", ""))
            self.assertNotIn("no-store", response.headers.get("cache-control", ""))
