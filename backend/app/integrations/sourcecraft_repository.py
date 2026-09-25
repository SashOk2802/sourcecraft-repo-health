"""Безопасное разрешение публичных репозиториев SourceCraft для production worker."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import quote

from backend.app.analysis.dispatch import AnalysisPrincipal
from backend.app.contracts import AnalysisContext, RepositoryRef
from backend.app.integrations.sourcecraft import (
    SourceCraftClient,
    SourceCraftClientError,
    SourceCraftRequestError,
)

_SOURCECRAFT_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PUBLIC_VISIBILITY = "public"
_DEFAULT_PERIOD = timedelta(days=365)
_ORGANIZATIONS_ENV = "SOURCECRAFT_PUBLIC_ORGANIZATIONS"
_TOKEN_ENV = "SOURCECRAFT_TOKEN"


class SourceCraftRepositoryUnavailableError(RuntimeError):
    """Каталог SourceCraft недоступен или вернул непригодный для анализа ответ."""


class SourceCraftCatalogClient(Protocol):
    """Минимальная часть клиента, нужная resolver для публичного каталога."""

    def get_paginated_objects(
        self,
        path: str,
        *,
        items_field: str,
        page_size: int = 100,
        max_pages: int = 100,
    ) -> list[dict[str, object]]:
        """Возвращает все элементы каталога."""

    def close(self) -> None:
        """Закрывает созданные HTTP-ресурсы."""


CatalogClientFactory = Callable[[str], SourceCraftCatalogClient]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class SourceCraftPublicCatalogSettings:
    """Явная настройка каталога, в котором разрешён только public-анализ."""

    token: str
    organization_slugs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.token, str) or not self.token.strip():
            raise ValueError("SourceCraft token must not be blank")
        if not self.organization_slugs:
            raise ValueError("at least one public SourceCraft organization is required")
        if any(
            not isinstance(slug, str) or not _SOURCECRAFT_SLUG.fullmatch(slug)
            for slug in self.organization_slugs
        ):
            raise ValueError("SourceCraft organization slugs must be safe path segments")
        if len(set(self.organization_slugs)) != len(self.organization_slugs):
            raise ValueError("SourceCraft organization slugs must be unique")


class SourceCraftPublicRepositoryResolver:
    """Строит контекст лишь для публичного репозитория из настроенного каталога.

    До появления пользовательского подключения SourceCraft сервисный токен не
    даёт право анализировать private/internal репозитории: такой запрос
    завершается PermissionError, даже если токен backend технически имеет доступ.
    """

    def __init__(
        self,
        settings: SourceCraftPublicCatalogSettings,
        *,
        client_factory: CatalogClientFactory = SourceCraftClient,
        clock: Clock | None = None,
        analysis_period: timedelta = _DEFAULT_PERIOD,
    ) -> None:
        if analysis_period <= timedelta():
            raise ValueError("analysis_period must be positive")
        self._settings = settings
        self._client_factory = client_factory
        self._clock = clock or _utc_now
        self._analysis_period = analysis_period

    async def resolve(
        self,
        repository_id: str,
        principal: AnalysisPrincipal,
    ) -> AnalysisContext:
        """Проверяет public-видимость и собирает AnalysisContext вне event loop."""

        if not isinstance(repository_id, str) or not repository_id.strip():
            raise ValueError("repository_id must not be blank")
        if not isinstance(principal, AnalysisPrincipal):
            raise TypeError("principal must be an AnalysisPrincipal")

        # Principal намеренно не используется для расширения доступа: на этом
        # этапе доступны только public-репозитории, одинаковые для всех.
        return await asyncio.to_thread(self._resolve_sync, repository_id.strip())

    def _resolve_sync(self, repository_id: str) -> AnalysisContext:
        client = self._client_factory(self._settings.token)
        try:
            repository = self._find_repository(client, repository_id)
        except SourceCraftClientError as error:
            raise SourceCraftRepositoryUnavailableError(
                "SourceCraft repository catalog is unavailable"
            ) from error
        except (LookupError, PermissionError, SourceCraftRepositoryUnavailableError):
            raise
        except Exception as error:
            raise SourceCraftRepositoryUnavailableError(
                "SourceCraft repository catalog is unavailable"
            ) from error
        finally:
            client.close()

        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        now = now.astimezone(UTC)
        return AnalysisContext(
            repository=repository.repository,
            # Каталог гарантирует default_branch, который LocalGitRepository
            # передаёт git как ref. Commit SHA появится после расширения API.
            commit_sha=repository.default_branch,
            analyzed_at=now,
            period_start=now - self._analysis_period,
            period_end=now,
        )

    def _find_repository(
        self,
        client: SourceCraftCatalogClient,
        repository_id: str,
    ) -> _ResolvedRepository:
        for organization_slug in self._settings.organization_slugs:
            path = f"/orgs/{quote(organization_slug, safe='')}/repos"
            repositories = client.get_paginated_objects(
                path,
                items_field="repositories",
                page_size=100,
            )
            for payload in repositories:
                if payload.get("id") != repository_id:
                    continue
                return _parse_public_repository(payload, organization_slug)
        raise LookupError("SourceCraft repository was not found")


@dataclass(frozen=True, slots=True)
class _ResolvedRepository:
    repository: RepositoryRef
    default_branch: str


def _parse_public_repository(
    payload: dict[str, object],
    expected_organization_slug: str,
) -> _ResolvedRepository:
    if payload.get("visibility") != _PUBLIC_VISIBILITY:
        raise PermissionError("SourceCraft repository is not public")

    identifier = _required_string(repository_id="", value=payload.get("id"))
    repository_slug = _required_slug(payload.get("slug"), "repository slug")
    organization = payload.get("organization")
    if not isinstance(organization, dict):
        raise SourceCraftRepositoryUnavailableError(
            "SourceCraft repository organization is invalid"
        )
    organization_slug = _required_slug(organization.get("slug"), "organization slug")
    if organization_slug != expected_organization_slug:
        raise SourceCraftRepositoryUnavailableError(
            "SourceCraft repository organization is invalid"
        )

    web_url = payload.get("web_url")
    if web_url is not None and not isinstance(web_url, str):
        raise SourceCraftRepositoryUnavailableError("SourceCraft repository web URL is invalid")
    try:
        SourceCraftClient.resolve_git_clone_url(
            organization_slug,
            repository_slug,
            web_url,
        )
    except SourceCraftRequestError as error:
        raise SourceCraftRepositoryUnavailableError(
            "SourceCraft repository web URL is invalid"
        ) from error

    default_branch = _required_slug(payload.get("default_branch"), "default branch")
    return _ResolvedRepository(
        repository=RepositoryRef(
            id=identifier,
            organization_slug=organization_slug,
            repository_slug=repository_slug,
            web_url=web_url,
        ),
        default_branch=default_branch,
    )


def create_sourcecraft_public_repository_resolver_from_environment(
    environ: dict[str, str] | None = None,
) -> SourceCraftPublicRepositoryResolver | None:
    """Создаёт public-only resolver при полной явной конфигурации окружения."""

    values = os.environ if environ is None else environ
    token = values.get(_TOKEN_ENV, "").strip()
    raw_organizations = values.get(_ORGANIZATIONS_ENV, "")
    organization_slugs = tuple(
        slug.strip() for slug in raw_organizations.split(",") if slug.strip()
    )
    if not token and not organization_slugs:
        return None
    if not token or not organization_slugs:
        raise ValueError(f"{_TOKEN_ENV} and {_ORGANIZATIONS_ENV} must be configured together")
    return SourceCraftPublicRepositoryResolver(
        SourceCraftPublicCatalogSettings(
            token=token,
            organization_slugs=organization_slugs,
        )
    )


def _required_string(*, repository_id: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceCraftRepositoryUnavailableError(
            f"SourceCraft repository {repository_id or 'payload'} is invalid"
        )
    return value.strip()


def _required_slug(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SOURCECRAFT_SLUG.fullmatch(value):
        raise SourceCraftRepositoryUnavailableError(f"SourceCraft {field_name} is invalid")
    return value


def _utc_now() -> datetime:
    return datetime.now(UTC)
