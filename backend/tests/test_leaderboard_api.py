from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

import httpx

from backend.app.analysis import InMemoryAnalysisStore, run_analysis
from backend.app.analysis.runner import AnalyzerRegistration
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef
from backend.app.integrations.sourcecraft_repositories import SourceCraftRepository
from backend.app.leaderboard import LeaderboardService, PublicRepositoryMetadata
from backend.app.main import create_app
from backend.app.scoring.methodology import CATEGORY_WEIGHTS


class FakePublicRepositoryCatalog:
    def __init__(self, repositories: tuple[PublicRepositoryMetadata, ...]) -> None:
        self._repositories = repositories

    async def list_repositories(self) -> tuple[PublicRepositoryMetadata, ...]:
        return self._repositories


class SourceCraftCatalog:
    def __init__(self, repositories: tuple[SourceCraftRepository, ...]) -> None:
        self._repositories = repositories

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        return self._repositories


class LeaderboardApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.timestamp = datetime(2026, 9, 25, 12, tzinfo=UTC)
        self.store = InMemoryAnalysisStore()
        repositories = (
            repository("alpha", "Python", likes=42),
            repository("preview", "Rust", likes=None),
            repository("pending", "Go", likes=1),
        )
        await self.store.save("analysis-alpha", execution("alpha", self.timestamp, all_scores=90))
        await self.store.save(
            "analysis-preview",
            execution("preview", self.timestamp - timedelta(hours=1), single_score=75),
        )
        self.app = create_app(
            analysis_store=self.store,
            leaderboard_service=LeaderboardService(
                analysis_store=self.store,
                repository_catalog=FakePublicRepositoryCatalog(repositories),
            ),
        )

    async def test_returns_frontend_contract_with_ranked_partial_scores(
        self,
    ) -> None:
        async with api_client(self.app) as client:
            response = await client.get("/api/v1/leaderboard?page=1&pageSize=15")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [
                (item["repository"]["id"], item["place"], item["score"], item["isPreliminary"])
                for item in payload["items"]
            ],
            [("alpha", 1, 90.0, False), ("preview", 2, 75.0, True)],
        )
        self.assertEqual(payload["preliminary"], [])
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["partialTotal"], 1)
        self.assertEqual(payload["preliminaryTotal"], 0)
        self.assertEqual(payload["languages"], [{"name": "Python", "count": 1}, {"name": "Rust", "count": 1}])
        self.assertEqual(payload["pendingCount"], 1)

    async def test_returns_partial_rows_with_place_even_when_legacy_block_is_requested(self) -> None:
        async with api_client(self.app) as client:
            response = await client.get("/api/v1/leaderboard?includePreliminary=true")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["preliminary"], [])
        preview = next(item for item in payload["items"] if item["analysisId"] == "analysis-preview")
        self.assertEqual(preview["place"], 2)
        self.assertEqual(preview["score"], 75.0)
        self.assertTrue(preview["isPreliminary"])
        self.assertEqual(preview["likes"], None)

    async def test_uses_sourcecraft_public_catalog_for_default_service(self) -> None:
        app = create_app(
            analysis_store=self.store,
            repository_catalog=SourceCraftCatalog(
                (sourcecraft_repository("alpha", language="Python"),)
            ),
        )

        async with api_client(app) as client:
            response = await client.get("/api/v1/leaderboard")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["repository"]["id"], "alpha")
        self.assertEqual(response.json()["items"][0]["repository"]["language"], "Python")

    async def test_returns_503_until_public_catalog_is_configured(self) -> None:
        app = create_app(analysis_store=InMemoryAnalysisStore())

        async with api_client(app) as client:
            response = await client.get("/api/v1/leaderboard")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Leaderboard is not configured."})

    async def test_rejects_invalid_query_parameters(self) -> None:
        async with api_client(self.app) as client:
            page_response = await client.get("/api/v1/leaderboard?page=0")
            sort_response = await client.get("/api/v1/leaderboard?sort=unknown")

        self.assertEqual(page_response.status_code, 422)
        self.assertEqual(sort_response.status_code, 422)


def sourcecraft_repository(identifier: str, *, language: str | None) -> SourceCraftRepository:
    return SourceCraftRepository(
        id=identifier,
        name=identifier,
        organization_slug="team",
        slug=identifier,
        default_branch="main",
        visibility="public",
        is_empty=False,
        language=language,
        branch_count=1,
        web_url=f"https://sourcecraft.dev/team/{identifier}",
    )


def repository(
    identifier: str,
    language: str,
    *,
    likes: int | None,
) -> PublicRepositoryMetadata:
    return PublicRepositoryMetadata(
        repository_id=identifier,
        organization_slug="team",
        repository_slug=identifier,
        url=f"https://sourcecraft.dev/team/{identifier}",
        language=language,
        likes=likes,
        last_activity_at=datetime(2026, 9, 24, 12, tzinfo=UTC),
    )


def execution(
    repository_id: str,
    analyzed_at: datetime,
    *,
    all_scores: float | None = None,
    single_score: float | None = None,
):
    context = AnalysisContext(
        repository=RepositoryRef(
            repository_id,
            "team",
            repository_id,
            f"https://sourcecraft.dev/team/{repository_id}",
        ),
        commit_sha="a" * 40,
        analyzed_at=analyzed_at,
        period_start=analyzed_at - timedelta(days=365),
        period_end=analyzed_at,
    )
    scores = (
        {category: all_scores for category in CATEGORY_WEIGHTS}
        if all_scores is not None
        else {"activity": single_score}
        if single_score is not None
        else {}
    )
    registrations = tuple(
        AnalyzerRegistration(
            category,
            lambda _, category=category, score=score: CategoryResult(
                category=category,
                status=DataStatus.MEASURED,
                score=score,
                summary="Категория рассчитана.",
            ),
        )
        for category, score in scores.items()
    )
    return run_analysis(context, registrations)


def api_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )
