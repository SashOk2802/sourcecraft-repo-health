"""Адаптация безопасного каталога SourceCraft к публичному рейтингу."""

from __future__ import annotations

from backend.app.integrations.sourcecraft_public_catalog import (
    PublicRepositoryCatalog as SourceCraftPublicRepositoryCatalog,
)
from backend.app.leaderboard.snapshot_projection import PublicRepositoryMetadata


class SourceCraftLeaderboardRepositoryCatalog:
    """Преобразует подтверждённые public-репозитории в метаданные рейтинга."""

    def __init__(self, catalog: SourceCraftPublicRepositoryCatalog) -> None:
        self._catalog = catalog

    async def list_repositories(self) -> tuple[PublicRepositoryMetadata, ...]:
        """Возвращает только поля, которые разрешено показывать в рейтинге."""

        repositories = await self._catalog.list_repositories()
        if any(repository.visibility != "public" for repository in repositories):
            raise ValueError("public repository catalog returned a non-public repository")
        return tuple(
            PublicRepositoryMetadata(
                repository_id=repository.id,
                organization_slug=repository.organization_slug,
                repository_slug=repository.slug,
                url=repository.web_url,
                language=repository.language,
                likes=repository.likes,
                last_activity_at=repository.last_activity_at,
            )
            for repository in repositories
        )
