"""Проверяет проекцию public-каталога SourceCraft в поля рейтинга."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from backend.app.integrations.sourcecraft_repositories import SourceCraftRepository
from backend.app.leaderboard.sourcecraft_catalog import SourceCraftLeaderboardRepositoryCatalog


class SourceCraftLeaderboardRepositoryCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_passes_validated_reactions_and_last_activity_to_public_metadata(self) -> None:
        catalog = SourceCraftLeaderboardRepositoryCatalog(
            _Catalog(
                SourceCraftRepository(
                    id="repository-1",
                    name="public-repository",
                    organization_slug="example-org",
                    slug="public-repository",
                    default_branch="main",
                    visibility="public",
                    is_empty=False,
                    language="Python",
                    branch_count=2,
                    web_url="https://sourcecraft.dev/example-org/public-repository",
                    likes=6,
                    last_activity_at=datetime(2026, 9, 27, 12, 30, tzinfo=UTC),
                )
            )
        )

        (metadata,) = await catalog.list_repositories()

        self.assertEqual(metadata.likes, 6)
        self.assertEqual(metadata.last_activity_at, datetime(2026, 9, 27, 12, 30, tzinfo=UTC))

    async def test_rejects_non_public_repository_before_it_can_reach_rating(self) -> None:
        catalog = SourceCraftLeaderboardRepositoryCatalog(
            _Catalog(
                SourceCraftRepository(
                    id="repository-1",
                    name="private-repository",
                    organization_slug="example-org",
                    slug="private-repository",
                    default_branch="main",
                    visibility="private",
                    is_empty=False,
                    language=None,
                    branch_count=0,
                    web_url=None,
                )
            )
        )

        with self.assertRaisesRegex(ValueError, "non-public"):
            await catalog.list_repositories()


class _Catalog:
    def __init__(self, *repositories: SourceCraftRepository) -> None:
        self._repositories = repositories

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        return self._repositories
