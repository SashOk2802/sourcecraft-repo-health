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

    async def test_returns_frontend_contract_with_pagination_and_hidden_preliminary_block(
        self,
    ) -> None:
        async with api_client(self.app) as client:
            response = await client.get("/api/v1/leaderboard?page=1&pageSize=15")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "items": [
                    {
                        "place": 1,
                        "analysisId": "analysis-alpha",
                        "repository": {
                            "id": "alpha",
                            "organizationSlug": "team",
                            "repositorySlug": "alpha",
                            "name": "team/alpha",
                            "url": "https://sourcecraft.dev/team/alpha",
                            "description": None,
                            "language": "Python",
                        },
                        "score": 90.0,
                        "coverage": 1.0,
                        "isPreliminary": False,
                        "scoreLimited": False,
                        "likes": 42,
                        "lastActivityAt": "2026-09-24T12:00:00Z",
                        "analyzedAt": "2026-09-25T12:00:00Z",
                        "categories": [
                            {
                                "code": category,
                                "label": label,
                                "status": "measured",
                                "score": 90.0,
                            }
                            for category, label in (
                                ("security", "Безопасность"),
                                ("cicd", "CI/CD"),
                                ("documentation", "Документация"),
                                ("activity", "Активность"),
                                ("issues", "Работа с issues"),
                                ("code_health", "Состояние кода"),
                            )
                        ],
                    }
                ],
                "preliminary": [],
                "total": 1,
                "preliminaryTotal": 1,
                "page": 1,
                "pageSize": 15,
                "languages": [{"name": "Python", "count": 1}, {"name": "Rust", "count": 1}],
                "updatedAt": "2026-09-25T12:00:00Z",
                "pendingCount": 1,
                "methodologyVersion": "v1",
            },
        )

    async def test_returns_requested_preliminary_rows_without_place(self) -> None:
        async with api_client(self.app) as client:
            response = await client.get("/api/v1/leaderboard?includePreliminary=true")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["preliminary"]), 1)
        self.assertEqual(payload["preliminary"][0]["place"], None)
        self.assertEqual(payload["preliminary"][0]["analysisId"], "analysis-preview")
        self.assertEqual(payload["preliminary"][0]["score"], 75.0)
        self.assertTrue(payload["preliminary"][0]["isPreliminary"])
        self.assertEqual(payload["preliminary"][0]["likes"], None)

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
