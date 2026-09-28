"""Тестирует генерацию SVG-бейджей и HTTP-эндпоинты для README."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime

import httpx

from backend.app.analysis import run_analysis
from backend.app.analysis.runner import AnalysisExecution
from backend.app.analysis.store import InMemoryAnalysisStore
from backend.app.contracts import AnalysisContext, RepositoryRef
from backend.app.main import create_app
from backend.app.reporting.badge import render_score_badge


def _sample_execution(
    *,
    repository_id: str = "repo-42",
    organization_slug: str = "test-org",
    repository_slug: str = "test-repo",
    score: int | None = 85,
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

    def test_no_data_badge_is_gray(self) -> None:
        svg = render_score_badge(None, is_preliminary=False)
        self.assertIn("#6a737d", svg)
        self.assertIn("no data", svg)

    def test_xml_escaping(self) -> None:
        svg = render_score_badge(label="foo & <bar>", value_override="test & 'quote'")
        self.assertIn("foo &amp; &lt;bar&gt;", svg)
        self.assertIn("test &amp; 'quote'", svg)


class BadgeApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = InMemoryAnalysisStore()
        await self.store.start()
        self.app = create_app(analysis_store=self.store)

    async def asyncTearDown(self) -> None:
        await self.store.close()

    async def test_analysis_badge_returns_svg_with_cors_headers(self) -> None:
        execution = _sample_execution(score=92)
        await self.store.save("analysis-1", execution)

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

    async def test_missing_analysis_badge_returns_not_found_svg(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/analyses/unknown-id/badge.svg")

        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response.headers.get("content-type", ""))
        self.assertIn("not found", response.text)
        self.assertIn("#6a737d", response.text)

    async def test_repository_badge_returns_latest_score_svg(self) -> None:
        execution = _sample_execution(
            repository_id="repo-42",
            organization_slug="my-org",
            repository_slug="my-repo",
            score=73,
        )
        await self.store.save("analysis-1", execution)

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

    async def test_missing_repository_badge_returns_unknown_svg(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/repositories/missing-org/missing-repo/badge.svg")

        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response.headers.get("content-type", ""))
        self.assertIn("unknown", response.text)
        self.assertIn("#6a737d", response.text)
