"""Безопасный каталог репозиториев организации SourceCraft."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from backend.app.contracts import RepositoryRef
from backend.app.integrations.sourcecraft import (
    SourceCraftClient,
    SourceCraftRequestError,
    SourceCraftResponseError,
)

_SOURCECRAFT_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SOURCECRAFT_WEB_HOST = "sourcecraft.dev"
_VISIBILITIES = frozenset({"public", "internal", "private"})
_UINT64_MAX = 2**64 - 1
_REACTION_TYPES = frozenset({"none", "positive_low", "positive_medium", "positive_high"})
_POSITIVE_REACTION_TYPES = _REACTION_TYPES - {"none"}
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

REPOSITORY_PAGE_SIZE = 100
REPOSITORY_MAX_PAGES = 100
RepositoryVisibility = Literal["public", "internal", "private"]


@dataclass(frozen=True, slots=True)
class SourceCraftRepository:
    """Проверенные поля каталога, нужные сервису здоровья репозиториев.

    Идентификаторы и приватные метаданные скрыты из ``repr``, чтобы случайный
    журнал объекта не раскрыл имя закрытого репозитория.
    """

    id: str = field(repr=False)
    name: str = field(repr=False)
    organization_slug: str = field(repr=False)
    slug: str = field(repr=False)
    default_branch: str = field(repr=False)
    visibility: RepositoryVisibility
    is_empty: bool
    language: str | None = field(repr=False)
    branch_count: int
    web_url: str | None = field(repr=False)
    # Публичные реакции и время последнего изменения нужны для сортировок рейтинга.
    likes: int | None = None
    last_activity_at: datetime | None = field(default=None, repr=False)

    def as_repository_ref(self) -> RepositoryRef:
        """Возвращает минимальную ссылку для общего контекста анализа."""

        return RepositoryRef(
            id=self.id,
            organization_slug=self.organization_slug,
            repository_slug=self.slug,
            web_url=self.web_url,
        )


class SourceCraftRepositoryCatalogClient:
    """Читает доступные репозитории одной организации через REST API."""

    def __init__(self, sourcecraft_client: SourceCraftClient) -> None:
        self._sourcecraft_client = sourcecraft_client

    def list_repositories(
        self,
        organization_slug: str,
    ) -> tuple[SourceCraftRepository, ...]:
        """Возвращает проверенный полный список в пределах заданного бюджета."""

        _validate_slug(organization_slug, "organization_slug")
        payloads = self._sourcecraft_client.get_paginated_objects(
            f"/orgs/{organization_slug}/repos",
            items_field="repositories",
            page_size=REPOSITORY_PAGE_SIZE,
            max_pages=REPOSITORY_MAX_PAGES,
        )
        repositories = tuple(
            _parse_repository(payload, expected_organization_slug=organization_slug)
            for payload in payloads
        )
        _validate_unique_repositories(repositories)
        return repositories


def _parse_repository(
    payload: dict[str, Any],
    *,
    expected_organization_slug: str,
) -> SourceCraftRepository:
    repository_id = _require_string(payload, "id")
    name = _require_string(payload, "name")
    slug = _require_string(payload, "slug")
    _validate_response_slug(slug, "repository slug")

    organization = payload.get("organization")
    if not isinstance(organization, dict):
        raise SourceCraftResponseError("SourceCraft repository must contain an organization")
    organization_slug = _require_string(organization, "slug")
    _validate_response_slug(organization_slug, "organization slug")
    if organization_slug != expected_organization_slug:
        raise SourceCraftResponseError(
            "SourceCraft repository belongs to an unexpected organization"
        )

    visibility = payload.get("visibility")
    if not isinstance(visibility, str) or visibility not in _VISIBILITIES:
        raise SourceCraftResponseError("SourceCraft repository has an unknown visibility")

    is_empty = payload.get("is_empty")
    if not isinstance(is_empty, bool):
        raise SourceCraftResponseError("SourceCraft repository is_empty must be a boolean")

    default_branch = payload.get("default_branch")
    if not isinstance(default_branch, str) or (not is_empty and not default_branch):
        raise SourceCraftResponseError(
            "SourceCraft non-empty repository must contain a default branch"
        )

    language = _parse_language(payload.get("language"))
    likes = _parse_likes(payload.get("rating"))
    last_activity_at = _parse_last_activity_at(payload.get("last_updated"))

    counters = payload.get("counters")
    if not isinstance(counters, dict):
        raise SourceCraftResponseError("SourceCraft repository must contain counters")
    branch_count = _parse_uint64(counters.get("branches"), "branch counter")

    web_url = _parse_web_url(
        payload.get("web_url"),
        organization_slug=organization_slug,
        repository_slug=slug,
    )

    return SourceCraftRepository(
        id=repository_id,
        name=name,
        organization_slug=organization_slug,
        slug=slug,
        default_branch=default_branch,
        visibility=visibility,
        is_empty=is_empty,
        language=language,
        branch_count=branch_count,
        web_url=web_url,
        likes=likes,
        last_activity_at=last_activity_at,
    )


def _validate_slug(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SOURCECRAFT_SLUG.fullmatch(value):
        raise SourceCraftRequestError(
            f"SourceCraft {field_name} must be a safe URL path segment"
        )


def _validate_response_slug(value: str, field_name: str) -> None:
    if not _SOURCECRAFT_SLUG.fullmatch(value):
        raise SourceCraftResponseError(f"SourceCraft returned an invalid {field_name}")


def _require_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise SourceCraftResponseError(
            f"SourceCraft repository must contain a non-empty string {field_name}"
        )
    return value


def _parse_language(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SourceCraftResponseError("SourceCraft repository language must be an object or null")
    name = value.get("name")
    if not isinstance(name, str) or not name:
        raise SourceCraftResponseError(
            "SourceCraft repository language must contain a non-empty name"
        )
    return name


def _parse_likes(value: object) -> int | None:
    """Суммирует только положительные публичные реакции репозитория."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise SourceCraftResponseError("SourceCraft repository rating must be an object or null")
    reaction_counts = value.get("reaction_counts")
    if reaction_counts is None:
        return None
    if not isinstance(reaction_counts, list):
        raise SourceCraftResponseError("SourceCraft repository reaction_counts must be an array")

    seen_types: set[str] = set()
    likes = 0
    for reaction in reaction_counts:
        if not isinstance(reaction, dict):
            raise SourceCraftResponseError("SourceCraft repository reaction must be an object")
        reaction_type = reaction.get("type")
        if not isinstance(reaction_type, str) or reaction_type not in _REACTION_TYPES:
            raise SourceCraftResponseError("SourceCraft repository reaction has an unknown type")
        if reaction_type in seen_types:
            raise SourceCraftResponseError("SourceCraft repository contains duplicate reaction types")
        seen_types.add(reaction_type)
        count = _parse_uint64(reaction.get("count"), "reaction counter")
        if reaction_type in _POSITIVE_REACTION_TYPES:
            likes += count
            if likes > _UINT64_MAX:
                raise SourceCraftResponseError("SourceCraft repository likes must be uint64")
    return likes


def _parse_last_activity_at(value: object) -> datetime | None:
    """Проверяет RFC3339-время последнего изменения из public API."""

    if value is None:
        return None
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise SourceCraftResponseError("SourceCraft repository last_updated must be RFC3339 or null")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise SourceCraftResponseError(
            "SourceCraft repository last_updated must be RFC3339 or null"
        ) from error
    return parsed.astimezone(UTC)


def _parse_uint64(value: object, field_name: str) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise SourceCraftResponseError(f"SourceCraft repository {field_name} must be uint64")
    parsed = int(value)
    if parsed > _UINT64_MAX:
        raise SourceCraftResponseError(f"SourceCraft repository {field_name} must be uint64")
    return parsed


def _parse_web_url(
    value: object,
    *,
    organization_slug: str,
    repository_slug: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SourceCraftResponseError("SourceCraft repository web_url must be a string or null")
    # urlsplit() удаляет \n, \r и \t перед разбором. Проверяем исходную
    # строку, чтобы в отчёт не попал URL с управляющими символами.
    if _CONTROL_CHARACTERS.search(value):
        raise SourceCraftResponseError("SourceCraft repository web_url is not an official URL")

    try:
        parsed = urlsplit(value)
        is_safe = (
            parsed.scheme == "https"
            and parsed.hostname == _SOURCECRAFT_WEB_HOST
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and parsed.query == ""
            and parsed.fragment == ""
            and parsed.path.rstrip("/") == f"/{organization_slug}/{repository_slug}"
        )
    except ValueError:
        is_safe = False
    if not is_safe:
        raise SourceCraftResponseError("SourceCraft repository web_url is not an official URL")
    return value


def _validate_unique_repositories(
    repositories: tuple[SourceCraftRepository, ...],
) -> None:
    ids: set[str] = set()
    slugs: set[tuple[str, str]] = set()
    for repository in repositories:
        repository_slug = (repository.organization_slug, repository.slug)
        if repository.id in ids or repository_slug in slugs:
            raise SourceCraftResponseError(
                "SourceCraft returned a duplicate repository across catalog pages"
            )
        ids.add(repository.id)
        slugs.add(repository_slug)
