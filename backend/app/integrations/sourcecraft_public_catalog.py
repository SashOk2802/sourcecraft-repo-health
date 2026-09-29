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
    is_temporary_sourcecraft_error,
)

_PUBLIC_VISIBILITY = "public"
# Сколько репозиториев вне настроенного каталога можно добавить через публичный API.
_MAX_DISCOVERED = 100


class PublicRepositoryCatalog(Protocol):
    """Выдаёт только репозитории, которые безопасно показать любому пользователю."""

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        """Возвращает публичные репозитории разрешённых организаций."""


SourceCraftClientFactory = Callable[[str], SourceCraftClient]


class SourceCraftPublicRepositoryCatalog:
    """Читает настроенный public-каталог SourceCraft вне event loop.

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
        # Публичные репозитории вне настроенных организаций, которые запросили через
        # публичный API. Живут до перезапуска; публичность перепроверяется при каждом чтении.
        self._discovered: dict[str, tuple[str, str]] = {}

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        """Не блокирует event loop синхронным HTTP-клиентом SourceCraft."""

        listed = await asyncio.to_thread(self._list_repositories_sync)
        if not self._discovered:
            return listed
        discovered = await asyncio.to_thread(self._verify_discovered_sync, listed)
        return tuple(
            sorted(
                (*listed, *discovered),
                key=lambda repository: (repository.organization_slug, repository.slug, repository.id),
            )
        )

    async def discover(
        self,
        organization_slug: str,
        repository_slug: str,
    ) -> SourceCraftRepository | None:
        """Добавляет в каталог публичный репозиторий вне настроенных организаций.

        Репозиторий ищется в SourceCraft по организации и slug; добавляется только при
        visibility: public. Любая ошибка SourceCraft — None: вызывающий отвечает как раньше.
        """

        repository = await asyncio.to_thread(self._lookup_sync, organization_slug, repository_slug)
        if repository is None:
            return None
        if repository.id not in self._discovered and len(self._discovered) >= _MAX_DISCOVERED:
            return None
        self._discovered[repository.id] = (repository.organization_slug, repository.slug)
        return repository

    def _lookup_sync(self, organization_slug: str, repository_slug: str) -> SourceCraftRepository | None:
        client = self._client_factory(self._settings.token)
        try:
            repositories = SourceCraftRepositoryCatalogClient(client).list_repositories(organization_slug)
        except (SourceCraftClientError, ValueError):
            return None
        finally:
            client.close()
        return next(
            (
                repository
                for repository in repositories
                if repository.slug.casefold() == repository_slug.casefold()
                and repository.visibility == _PUBLIC_VISIBILITY
            ),
            None,
        )

    def _verify_discovered_sync(
        self,
        listed: tuple[SourceCraftRepository, ...],
    ) -> tuple[SourceCraftRepository, ...]:
        """Заново читает организации найденных репозиториев и оставляет только публичные."""

        known_ids = {repository.id for repository in listed}
        known_slugs = {(repository.organization_slug, repository.slug) for repository in listed}
        wanted = {
            repository_id: slugs
            for repository_id, slugs in self._discovered.items()
            if repository_id not in known_ids and slugs not in known_slugs
        }
        verified: list[SourceCraftRepository] = []
        failed_organizations: set[str] = set()
        for organization_slug in sorted({slugs[0] for slugs in wanted.values()}):
            client = self._client_factory(self._settings.token)
            try:
                repositories = SourceCraftRepositoryCatalogClient(client).list_repositories(organization_slug)
            except (SourceCraftClientError, ValueError):
                # Не удалось перепроверить — сейчас не выдаём, но и не забываем: сбой может быть временным.
                failed_organizations.add(organization_slug)
                continue
            finally:
                client.close()
            verified.extend(
                repository
                for repository in repositories
                if repository.id in wanted and repository.visibility == _PUBLIC_VISIBILITY
            )
        # Репозиторий закрыли или удалили — забываем его.
        verified_ids = {repository.id for repository in verified}
        for repository_id, (organization_slug, _) in wanted.items():
            if repository_id not in verified_ids and organization_slug not in failed_organizations:
                self._discovered.pop(repository_id, None)
        return tuple(verified)

    def _list_repositories_sync(self) -> tuple[SourceCraftRepository, ...]:
        client = self._client_factory(self._settings.token)
        try:
            catalog = SourceCraftRepositoryCatalogClient(client)
            if self._settings.discover_all_public:
                repositories = catalog.discover_public_repositories()
            else:
                repositories = tuple(
                    repository
                    for organization_slug in self._settings.organization_slugs
                    for repository in catalog.list_repositories(organization_slug)
                    if repository.visibility == _PUBLIC_VISIBILITY
                )
        except SourceCraftClientError as error:
            raise SourceCraftRepositoryUnavailableError(
                "SourceCraft repository catalog is unavailable",
                retryable=is_temporary_sourcecraft_error(error),
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
    """Создаёт каталог лишь при полной безопасной конфигурации окружения."""

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
