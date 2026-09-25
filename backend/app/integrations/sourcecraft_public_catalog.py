"""Авторизованный каталог только публичных репозиториев SourceCraft."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from backend.app.integrations.sourcecraft import SourceCraftClient, SourceCraftClientError
from backend.app.integrations.sourcecraft_repositories import (
    SourceCraftRepository,
    SourceCraftRepositoryCatalogClient,
)
from backend.app.integrations.sourcecraft_repository import (
    SourceCraftPublicCatalogSettings,
    SourceCraftRepositoryUnavailableError,
    create_sourcecraft_public_catalog_settings_from_environment,
)

_PUBLIC_VISIBILITY = "public"


class PublicRepositoryCatalog(Protocol):
    """Выдаёт только репозитории, которые безопасно показать любому пользователю."""

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        """Возвращает публичные репозитории разрешённых организаций."""


SourceCraftClientFactory = Callable[[str], SourceCraftClient]


class SourceCraftPublicRepositoryCatalog:
    """Читает настроенные SourceCraft-организации вне event loop.

    Сервисный PAT применяется только к запросу каталога. Результат перед
    отправкой в HTTP дополнительно фильтруется по ``visibility == public``.
    """

    def __init__(
        self,
        settings: SourceCraftPublicCatalogSettings,
        *,
        client_factory: SourceCraftClientFactory = SourceCraftClient,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        """Не блокирует event loop синхронным HTTP-клиентом SourceCraft."""

        return await asyncio.to_thread(self._list_repositories_sync)

    def _list_repositories_sync(self) -> tuple[SourceCraftRepository, ...]:
        client = self._client_factory(self._settings.token)
        try:
            catalog = SourceCraftRepositoryCatalogClient(client)
            repositories = tuple(
                repository
                for organization_slug in self._settings.organization_slugs
                for repository in catalog.list_repositories(organization_slug)
                if repository.visibility == _PUBLIC_VISIBILITY
            )
        except SourceCraftClientError as error:
            raise SourceCraftRepositoryUnavailableError(
                "SourceCraft repository catalog is unavailable"
            ) from error
        except Exception as error:
            raise SourceCraftRepositoryUnavailableError(
                "SourceCraft repository catalog is unavailable"
            ) from error
        finally:
            client.close()

        _ensure_unique_public_repositories(repositories)
        return tuple(
            sorted(
                repositories,
                key=lambda repository: (
                    repository.organization_slug,
                    repository.slug,
                    repository.id,
                ),
            )
        )


def create_sourcecraft_public_repository_catalog_from_environment(
    environ: dict[str, str] | None = None,
) -> SourceCraftPublicRepositoryCatalog | None:
    """Создаёт каталог лишь при полной паре безопасных переменных окружения."""

    settings = create_sourcecraft_public_catalog_settings_from_environment(environ)
    return SourceCraftPublicRepositoryCatalog(settings) if settings is not None else None


def _ensure_unique_public_repositories(
    repositories: tuple[SourceCraftRepository, ...],
) -> None:
    identifiers = {repository.id for repository in repositories}
    slugs = {(repository.organization_slug, repository.slug) for repository in repositories}
    if len(identifiers) != len(repositories) or len(slugs) != len(repositories):
        raise SourceCraftRepositoryUnavailableError(
            "SourceCraft repository catalog contains duplicate public repositories"
        )
