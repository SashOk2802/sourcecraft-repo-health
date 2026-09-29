from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from backend.app.analysis import InMemoryAnalysisStore, run_analysis
from backend.app.analysis.runner import AnalyzerRegistration
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef
from backend.app.leaderboard import (
    LeaderboardFilters,
    LeaderboardService,
    LeaderboardSort,
    PublicRepositoryMetadata,
)
from backend.app.scoring.methodology import CATEGORY_WEIGHTS


class FakePublicRepositoryCatalog:
    def __init__(self, repositories: tuple[PublicRepositoryMetadata, ...]) -> None:
        self._repositories = repositories

    async def list_repositories(self) -> tuple[PublicRepositoryMetadata, ...]:
        return self._repositories


class LeaderboardServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)
        self.repositories = (
            metadata("alpha", "Python", likes=10, activity=self.now - timedelta(days=2)),
            metadata("bravo", "Rust", likes=None, activity=None),
            metadata("preview", "Python", likes=100, activity=self.now),
            metadata("empty", "Go", likes=3, activity=self.now - timedelta(days=1)),
            metadata("pending", "Python", likes=1, activity=self.now - timedelta(days=3)),
        )
        self.store = InMemoryAnalysisStore()
        self.service = LeaderboardService(
            analysis_store=self.store,
            repository_catalog=FakePublicRepositoryCatalog(self.repositories),
        )
        await self.store.save(
            "analysis-alpha-v2",
            execution("alpha", self.now - timedelta(days=2), all_scores=95),
        )
        await self.store.save(
            "analysis-bravo-v2",
            execution("bravo", self.now - timedelta(days=1), all_scores=80),
        )
        await self.store.save(
            "analysis-preview-v2",
            execution("preview", self.now, single_score=90),
        )
        await self.store.save(
            "analysis-empty-v2",
            execution("empty", self.now - timedelta(hours=12)),
        )

    async def test_ranks_partial_scores_and_keeps_only_empty_scores_without_a_place(
        self,
    ) -> None:
        result = await self.service.get_page(page_size=1)

        self.assertEqual(result.total, 3)
        self.assertEqual(result.page, 1)
        self.assertEqual(result.page_size, 1)
        self.assertEqual(
            [(row.projection.repository.repository_id, row.rank) for row in result.entries],
            [("alpha", 1)],
        )
        self.assertEqual(
            [
                (row.projection.repository.repository_id, row.projection.score, row.rank)
                for row in result.preliminary_entries
            ],
            [("empty", None, None)],
        )
        self.assertEqual(result.preliminary_total, 1)
        self.assertEqual(result.partial_total, 1)
        self.assertEqual(result.pending_count, 1)
        self.assertEqual(
            [(facet.name, facet.count) for facet in result.languages],
            [("Python", 2), ("Go", 1), ("Rust", 1)],
        )
        self.assertEqual(result.updated_at, self.now)
        self.assertEqual(result.methodology_version, "v2")

    async def test_search_keeps_global_place_and_language_filter_does_not_recalculate_it(
        self,
    ) -> None:
        result = await self.service.get_page(
            filters=LeaderboardFilters(search="bravo", language="rust"),
        )

        self.assertEqual(
            [(row.projection.repository.repository_id, row.rank) for row in result.entries],
            [("bravo", 3)],
        )
        self.assertEqual(result.total, 1)
        self.assertEqual([(facet.name, facet.count) for facet in result.languages], [("Rust", 1)])

    async def test_uses_selected_methodology_without_mixing_versions(self) -> None:
        await self.store.save(
            "analysis-alpha-v1",
            execution(
                "alpha",
                self.now + timedelta(days=1),
                all_scores=100,
                methodology_version="v1",
            ),
        )

        version_two = await self.service.get_page()
        version_one = await self.service.get_page(methodology_version="v1")

        self.assertEqual(
            [(row.projection.analysis_id, row.rank) for row in version_two.entries],
            [
                ("analysis-alpha-v2", 1),
                ("analysis-preview-v2", 2),
                ("analysis-bravo-v2", 3),
            ],
        )
        self.assertEqual(
            [(row.projection.analysis_id, row.rank) for row in version_one.entries],
            [("analysis-alpha-v1", 1)],
        )
        self.assertEqual(version_one.pending_count, 4)

    async def test_paginates_all_scored_rows_but_returns_unscored_rows_separately(self) -> None:
        result = await self.service.get_page(
            sort=LeaderboardSort.LIKES,
            page=2,
            page_size=1,
        )

        self.assertEqual(
            [(row.projection.repository.repository_id, row.rank) for row in result.entries],
            [("alpha", 1)],
        )
        self.assertEqual(
            [row.projection.repository.repository_id for row in result.preliminary_entries],
            ["empty"],
        )

    async def test_places_unknown_likes_after_known_zero_in_one_ranked_block(self) -> None:
        repositories = (
            metadata("full-zero", "Python", likes=0, activity=self.now),
            metadata("full-unknown", "Python", likes=None, activity=self.now),
            metadata("preliminary-zero", "Python", likes=0, activity=self.now),
            metadata("preliminary-unknown", "Python", likes=None, activity=self.now),
        )
        store = InMemoryAnalysisStore()
        service = LeaderboardService(
            analysis_store=store,
            repository_catalog=FakePublicRepositoryCatalog(repositories),
        )
        await store.save("full-zero", execution("full-zero", self.now, all_scores=90))
        await store.save("full-unknown", execution("full-unknown", self.now, all_scores=80))
        await store.save(
            "preliminary-zero", execution("preliminary-zero", self.now, single_score=70)
        )
        await store.save(
            "preliminary-unknown",
            execution("preliminary-unknown", self.now, single_score=60),
        )

        result = await service.get_page(sort=LeaderboardSort.LIKES)

        self.assertEqual(
            [row.projection.repository.repository_id for row in result.entries],
            ["full-zero", "preliminary-zero", "full-unknown", "preliminary-unknown"],
        )
        self.assertEqual(result.preliminary_entries, ())
        self.assertEqual(result.partial_total, 2)

    async def test_rejects_invalid_page_arguments_and_duplicate_catalog_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "page must"):
            await self.service.get_page(page=0)
        with self.assertRaisesRegex(ValueError, "page_size"):
            await self.service.get_page(page_size=101)

        duplicate_catalog = FakePublicRepositoryCatalog(
            (self.repositories[0], self.repositories[0])
        )
        duplicate_service = LeaderboardService(
            analysis_store=self.store,
            repository_catalog=duplicate_catalog,
        )
        with self.assertRaisesRegex(ValueError, "duplicate repository IDs"):
            await duplicate_service.get_page()


def metadata(
    repository_id: str,
    language: str | None,
    *,
    likes: int | None,
    activity: datetime | None,
) -> PublicRepositoryMetadata:
    return PublicRepositoryMetadata(
        repository_id=repository_id,
        organization_slug="team",
        repository_slug=repository_id,
        url=f"https://sourcecraft.dev/team/{repository_id}",
        language=language,
        likes=likes,
        last_activity_at=activity,
    )


def execution(
    repository_id: str,
    analyzed_at: datetime,
    *,
    all_scores: float | None = None,
    single_score: float | None = None,
    methodology_version: str = "v2",
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
    if all_scores is not None:
        scores = {category: all_scores for category in CATEGORY_WEIGHTS}
    elif single_score is not None:
        scores = {"activity": single_score}
    else:
        scores = {}
    analyzers = tuple(
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
    result = run_analysis(context, analyzers)
    return replace(
        result,
        analysis=replace(result.analysis, methodology_version=methodology_version),
    )
