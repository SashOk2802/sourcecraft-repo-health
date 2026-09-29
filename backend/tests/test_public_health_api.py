"""Контракт и границы приватности публичного JSON API Repo Health."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx

from backend.app.analysis import InMemoryAnalysisStore, run_analysis
from backend.app.analysis.runner import AnalysisExecution, AnalyzerRegistration
from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    Evidence,
    Recommendation,
    RecommendationPriority,
    RepositoryRef,
)
from backend.app.integrations.sourcecraft_repositories import SourceCraftRepository
from backend.app.integrations.sourcecraft_repository import SourceCraftRepositoryUnavailableError
from backend.app.main import create_app
from backend.app.scoring.methodology import CATEGORY_LABELS, CATEGORY_WEIGHTS, METHODOLOGY_VERSION


class _Catalog:
    def __init__(self, repositories: tuple[SourceCraftRepository, ...] = ()) -> None:
        self.repositories = repositories

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        return self.repositories


class _UnavailableCatalog:
    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        raise SourceCraftRepositoryUnavailableError("SourceCraft is unavailable", retryable=True)


class PublicHealthApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.timestamp = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
        self.store = InMemoryAnalysisStore()
        self.catalog = _Catalog((_public_repository(),))
        self.app = create_app(analysis_store=self.store, repository_catalog=self.catalog)

    async def test_returns_minimal_public_projection_from_current_catalog(self) -> None:
        """JSON не должен быть скрытой копией приватного полного отчёта."""

        await self.store.save(
            "analysis-public",
            _execution(
                repository=RepositoryRef(
                    "repo-public",
                    "demo-org",
                    "health-api",
                    "https://untrusted.example/private-report",
                ),
                analyzed_at=self.timestamp,
                category_scores={category: 82 for category in CATEGORY_WEIGHTS},
                include_private_recommendation=True,
            ),
        )

        async with _api_client(self.app) as client:
            anonymous = await client.get("/api/v1/public/repositories/demo-org/health-api/health")
            authenticated = await client.get(
                "/api/v1/public/repositories/demo-org/health-api/health",
                headers={
                    "Authorization": "Bearer private-session-token",
                    "Cookie": "repo_health_session=private-cookie",
                },
            )

        self.assertEqual(anonymous.status_code, 200)
        self.assertEqual(authenticated.status_code, 200)
        self.assertEqual(authenticated.json(), anonymous.json())
        self.assertEqual(
            anonymous.json(),
            {
                "repository": {
                    "organizationSlug": "demo-org",
                    "repositorySlug": "health-api",
                    "url": "https://sourcecraft.dev/demo-org/health-api",
                    "language": "Python",
                },
                "score": 82.0,
                "coverage": 1.0,
                "isPreliminary": False,
                "scoreLimited": False,
                "analyzedAt": "2026-09-29T12:00:00Z",
                "methodologyVersion": METHODOLOGY_VERSION,
                "categories": [
                    {
                        "code": code,
                        "label": CATEGORY_LABELS[code],
                        "status": "measured",
                        "score": 82.0,
                    }
                    for code in CATEGORY_WEIGHTS
                ],
            },
        )

        rendered = anonymous.text
        for forbidden in (
            "analysisId",
            "recommendations",
            "private-session-token",
            "private-cookie",
            "private-finding-marker",
            "private-evidence-marker",
            "private-commit-marker",
            "untrusted.example",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)

    async def test_returns_current_preliminary_snapshot_without_a_score(self) -> None:
        await self.store.save(
            "analysis-no-score",
            _execution(
                repository=RepositoryRef("repo-public", "demo-org", "health-api"),
                analyzed_at=self.timestamp,
                category_scores={},
            ),
        )

        async with _api_client(self.app) as client:
            response = await client.get("/api/v1/public/repositories/demo-org/health-api/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["score"])
        self.assertEqual(payload["coverage"], 0.0)
        self.assertTrue(payload["isPreliminary"])
        self.assertEqual(payload["methodologyVersion"], METHODOLOGY_VERSION)
        self.assertEqual(
            [(category["status"], category["score"]) for category in payload["categories"]],
            [("unavailable", None)] * len(CATEGORY_WEIGHTS),
        )

    async def test_returns_the_same_404_for_unlisted_missing_and_unsuitable_snapshots(self) -> None:
        """Нельзя использовать API как oracle существования private-репозитория."""

        await self.store.save(
            "analysis-public",
            _execution(
                repository=RepositoryRef("repo-public", "demo-org", "health-api"),
                analyzed_at=self.timestamp,
                category_scores={category: 80 for category in CATEGORY_WEIGHTS},
            ),
        )
        old_version = _execution(
            repository=RepositoryRef("repo-old", "demo-org", "old-methodology"),
            analyzed_at=self.timestamp,
            category_scores={category: 80 for category in CATEGORY_WEIGHTS},
        )
        await self.store.save(
            "analysis-old",
            replace(old_version, analysis=replace(old_version.analysis, methodology_version="v1")),
        )
        await self.store.save(
            "analysis-spoofed",
            _execution(
                repository=RepositoryRef("repo-public", "other-org", "health-api"),
                analyzed_at=self.timestamp + timedelta(seconds=1),
                category_scores={category: 80 for category in CATEGORY_WEIGHTS},
            ),
        )

        async with _api_client(self.app) as client:
            missing = await client.get("/api/v1/public/repositories/demo-org/missing/health")

            self.catalog.repositories = ()
            unlisted = await client.get("/api/v1/public/repositories/demo-org/health-api/health")

            self.catalog.repositories = (_public_repository(),)
            spoofed = await client.get("/api/v1/public/repositories/demo-org/health-api/health")

            self.catalog.repositories = (_public_repository("repo-old", "old-methodology"),)
            obsolete = await client.get(
                "/api/v1/public/repositories/demo-org/old-methodology/health"
            )

        for response in (missing, unlisted, spoofed, obsolete):
            self.assertEqual(response.status_code, 404)
            self.assertNotIn("repo-public", response.text)
        self.assertEqual(missing.json(), unlisted.json())
        self.assertEqual(missing.json(), spoofed.json())
        self.assertEqual(missing.json(), obsolete.json())

    async def test_returns_503_for_unconfigured_or_unavailable_catalog(self) -> None:
        unconfigured_app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            configure_public_repository_catalog=False,
        )
        unavailable_app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            repository_catalog=_UnavailableCatalog(),
        )
        invalid_catalog_app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            repository_catalog=_Catalog((_private_repository(),)),
        )

        async with _api_client(unconfigured_app) as client:
            unconfigured = await client.get("/api/v1/public/repositories/demo-org/health-api/health")
        async with _api_client(unavailable_app) as client:
            unavailable = await client.get("/api/v1/public/repositories/demo-org/health-api/health")
        async with _api_client(invalid_catalog_app) as client:
            invalid_catalog = await client.get(
                "/api/v1/public/repositories/demo-org/health-api/health"
            )

        self.assertEqual(unconfigured.status_code, 503)
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(invalid_catalog.status_code, 503)

    async def test_success_response_is_cacheable_and_safe_to_embed_cross_origin(self) -> None:
        await self.store.save(
            "analysis-public",
            _execution(
                repository=RepositoryRef("repo-public", "demo-org", "health-api"),
                analyzed_at=self.timestamp,
                category_scores={category: 80 for category in CATEGORY_WEIGHTS},
            ),
        )

        async with _api_client(self.app) as client:
            response = await client.get(
                "/api/v1/public/repositories/demo-org/health-api/health",
                headers={"Origin": "https://consumer.example"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "*")
        self.assertNotIn("access-control-allow-credentials", response.headers)
        self.assertEqual(response.headers.get("cross-origin-resource-policy"), "cross-origin")
        self.assertEqual(response.headers.get("cache-control"), "public, max-age=300, s-maxage=300")
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")


def _public_repository(
    repository_id: str = "repo-public",
    repository_slug: str = "health-api",
) -> SourceCraftRepository:
    return SourceCraftRepository(
        id=repository_id,
        name=repository_slug,
        organization_slug="demo-org",
        slug=repository_slug,
        default_branch="main",
        visibility="public",
        is_empty=False,
        language="Python",
        branch_count=1,
        web_url=f"https://sourcecraft.dev/demo-org/{repository_slug}",
    )


def _private_repository() -> SourceCraftRepository:
    return replace(_public_repository(), visibility="private")


def _execution(
    *,
    repository: RepositoryRef,
    analyzed_at: datetime,
    category_scores: dict[str, float],
    include_private_recommendation: bool = False,
) -> AnalysisExecution:
    def evaluate(category: str, score: float) -> CategoryResult:
        recommendations = ()
        if include_private_recommendation and category == "security":
            recommendations = (
                Recommendation(
                    code="private-finding-marker",
                    priority=RecommendationPriority.P0,
                    problem="private-finding-marker",
                    action="Keep private-finding-marker private.",
                    rationale="private-evidence-marker",
                    evidence=(
                        Evidence(
                            source="sourcecraft-appsec",
                            reference="private-evidence-marker",
                            summary="private-evidence-marker",
                        ),
                    ),
                ),
            )
        return CategoryResult(
            category=category,
            status=DataStatus.MEASURED,
            score=score,
            summary="Calculated for the test.",
            recommendations=recommendations,
        )

    return run_analysis(
        AnalysisContext(
            repository=repository,
            commit_sha="private-commit-marker",
            analyzed_at=analyzed_at,
            period_start=analyzed_at,
            period_end=analyzed_at,
        ),
        tuple(
            AnalyzerRegistration(
                category,
                lambda _, category=category, score=score: evaluate(category, score),
            )
            for category, score in category_scores.items()
        ),
    )


def _api_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )
