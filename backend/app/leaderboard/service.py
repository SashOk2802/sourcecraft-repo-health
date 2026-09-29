"""Сборка страницы публичного рейтинга из каталога и снимков анализов."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from backend.app.analysis import AnalysisStore, StoredAnalysisSnapshot
from backend.app.leaderboard.policy import (
    LeaderboardFilters,
    LeaderboardSort,
    build_leaderboard,
)
from backend.app.leaderboard.snapshot_projection import (
    LeaderboardSnapshotProjection,
    PublicRepositoryMetadata,
    project_public_snapshot,
)
from backend.app.scoring.methodology import METHODOLOGY_VERSION

# Сколько последних снимков отдаёт публичная история Score.
PUBLIC_HISTORY_LIMIT = 20

_MAX_PAGE_SIZE = 100


class PublicRepositoryCatalog(Protocol):
    """Возвращает только подтверждённые публичные репозитории SourceCraft."""

    async def list_repositories(self) -> tuple[PublicRepositoryMetadata, ...]:
        """Читает неизменяемый снимок public-каталога для одной выдачи."""


@dataclass(frozen=True, slots=True)
class LeaderboardPageRow:
    """Проекция отчёта и вычисленное место, если у неё есть числовой Score."""

    projection: LeaderboardSnapshotProjection
    rank: int | None


@dataclass(frozen=True, slots=True)
class LeaderboardLanguageFacet:
    """Количество проанализированных публичных репозиториев одного языка."""

    name: str
    count: int


@dataclass(frozen=True, slots=True)
class LeaderboardPage:
    """Полный ответ use-case до сериализации в HTTP JSON."""

    entries: tuple[LeaderboardPageRow, ...]
    preliminary_entries: tuple[LeaderboardPageRow, ...]
    total: int
    preliminary_total: int
    partial_total: int
    page: int
    page_size: int
    languages: tuple[LeaderboardLanguageFacet, ...]
    updated_at: datetime | None
    pending_count: int
    methodology_version: str


class LeaderboardService:
    """Собирает сопоставимый публичный рейтинг одной версии методики."""

    def __init__(
        self,
        *,
        analysis_store: AnalysisStore,
        repository_catalog: PublicRepositoryCatalog,
    ) -> None:
        self._analysis_store = analysis_store
        self._repository_catalog = repository_catalog

    async def get_page(
        self,
        *,
        methodology_version: str = METHODOLOGY_VERSION,
        filters: LeaderboardFilters | None = None,
        sort: LeaderboardSort = LeaderboardSort.SCORE,
        page: int = 1,
        page_size: int = 15,
    ) -> LeaderboardPage:
        """Строит страницу рейтинга, не смешивая Score разных методик."""

        selected_version = _normalize_methodology_version(methodology_version)
        effective_filters = _normalize_filters(filters)
        _validate_pagination(page, page_size)
        if not isinstance(sort, LeaderboardSort):
            raise TypeError("sort must be a LeaderboardSort")

        repositories = tuple(await self._repository_catalog.list_repositories())
        metadata_by_id = _metadata_by_id(repositories)
        snapshots = await self._analysis_store.list_latest_for_repositories(tuple(metadata_by_id))
        projections = _project_snapshots(metadata_by_id, snapshots)
        selected = tuple(
            projection
            for projection in projections
            if projection.methodology_version == selected_version
        )

        candidates = tuple(
            projection.candidate for projection in selected if projection.candidate is not None
        )
        policy_result = build_leaderboard(
            candidates,
            methodology_version=selected_version,
            filters=effective_filters,
            sort=sort,
        )
        projections_by_key = {
            _projection_key(projection): projection
            for projection in selected
            if projection.candidate is not None
        }
        entries = tuple(
            LeaderboardPageRow(
                projection=projections_by_key[
                    (row.candidate.repository_id, row.candidate.methodology_version)
                ],
                rank=row.rank,
            )
            for row in policy_result.entries
        )
        preliminary_entries = _preliminary_rows(selected, effective_filters, sort)
        start = (page - 1) * page_size

        return LeaderboardPage(
            entries=entries[start : start + page_size],
            preliminary_entries=preliminary_entries,
            total=policy_result.total,
            preliminary_total=len(preliminary_entries),
            partial_total=policy_result.partial_total,
            page=page,
            page_size=page_size,
            languages=_language_facets(selected, effective_filters.search),
            updated_at=max((item.analyzed_at for item in selected), default=None),
            pending_count=len(
                set(metadata_by_id) - {item.repository.repository_id for item in selected}
            ),
            methodology_version=selected_version,
        )

    async def get_public_repository_snapshot(
        self,
        organization_slug: str,
        repository_slug: str,
        *,
        methodology_version: str = METHODOLOGY_VERSION,
    ) -> LeaderboardSnapshotProjection | None:
        """Возвращает последнюю пригодную публичную проекцию одного репозитория.

        Метод использует тот же каталог и ту же строгую проекцию, что и рейтинг.
        Поэтому public API не может выдать private/internal отчёт, снимок с
        устаревшими slug или результат другой версии методики.
        """

        selected_version = _normalize_methodology_version(methodology_version)
        organization = _normalize_slug(organization_slug)
        repository = _normalize_slug(repository_slug)
        if organization is None or repository is None:
            return None

        metadata_by_id = _metadata_by_id(await self._repository_catalog.list_repositories())
        matches = tuple(
            item
            for item in metadata_by_id.values()
            if item.organization_slug.casefold() == organization
            and item.repository_slug.casefold() == repository
        )
        if len(matches) != 1:
            return None

        metadata = matches[0]
        snapshots = await self._analysis_store.list_latest_for_repositories((metadata.repository_id,))
        projections = _project_snapshots({metadata.repository_id: metadata}, snapshots)
        current = tuple(
            projection
            for projection in projections
            if projection.methodology_version == selected_version
        )
        if len(current) != 1:
            return None
        return current[0]

    async def get_public_repository_history(
        self,
        organization_slug: str,
        repository_slug: str,
        *,
        limit: int = PUBLIC_HISTORY_LIMIT,
    ) -> tuple[LeaderboardSnapshotProjection, ...] | None:
        """Возвращает до limit последних публичных проекций репозитория, старые первыми.

        Публичность проверяется по каталогу SourceCraft при каждом вызове, как у
        get_public_repository_snapshot: private/internal и неизвестный репозиторий
        дают None. Каждый снимок проходит ту же строгую проекцию, что и рейтинг,
        поэтому снимок с другим id или устаревшими slug в историю не попадает.
        Пустой кортеж — репозиторий публичный, но снимков ещё нет.
        """

        organization = _normalize_slug(organization_slug)
        repository = _normalize_slug(repository_slug)
        if organization is None or repository is None or limit <= 0:
            return None

        metadata_by_id = _metadata_by_id(await self._repository_catalog.list_repositories())
        matches = tuple(
            item
            for item in metadata_by_id.values()
            if item.organization_slug.casefold() == organization
            and item.repository_slug.casefold() == repository
        )
        if len(matches) != 1:
            return None

        metadata = matches[0]
        snapshots = await self._analysis_store.list_recent_for_repository(metadata.repository_id, limit)
        projections = [
            projection
            for stored_snapshot in snapshots
            if (projection := project_public_snapshot(metadata, stored_snapshot)) is not None
        ]
        projections.sort(key=lambda projection: (projection.analyzed_at, projection.analysis_id))
        return tuple(projections[-limit:])

    async def get_public_repository_metadata(
        self,
        organization_slug: str,
        repository_slug: str,
    ) -> PublicRepositoryMetadata | None:
        """Проверяет существование репозитория в публичном каталоге без привязки к снимкам."""
        organization = _normalize_slug(organization_slug)
        repository = _normalize_slug(repository_slug)
        if organization is None or repository is None:
            return None

        metadata_by_id = _metadata_by_id(await self._repository_catalog.list_repositories())
        matches = tuple(
            item
            for item in metadata_by_id.values()
            if item.organization_slug.casefold() == organization
            and item.repository_slug.casefold() == repository
        )
        if len(matches) != 1:
            return None
        return matches[0]

def _metadata_by_id(
    repositories: Iterable[PublicRepositoryMetadata],
) -> dict[str, PublicRepositoryMetadata]:
    metadata_by_id: dict[str, PublicRepositoryMetadata] = {}
    slugs: set[tuple[str, str]] = set()
    for metadata in repositories:
        if not isinstance(metadata, PublicRepositoryMetadata):
            raise TypeError("repository catalog must return PublicRepositoryMetadata values")
        if metadata.repository_id in metadata_by_id:
            raise ValueError("repository catalog contains duplicate repository IDs")
        slug = (metadata.organization_slug, metadata.repository_slug)
        if slug in slugs:
            raise ValueError("repository catalog contains duplicate repository slugs")
        metadata_by_id[metadata.repository_id] = metadata
        slugs.add(slug)
    return metadata_by_id


def _project_snapshots(
    metadata_by_id: dict[str, PublicRepositoryMetadata],
    snapshots: Iterable[StoredAnalysisSnapshot],
) -> tuple[LeaderboardSnapshotProjection, ...]:
    projections: list[LeaderboardSnapshotProjection] = []
    keys: set[tuple[str, str]] = set()
    for stored_snapshot in snapshots:
        if not isinstance(stored_snapshot, StoredAnalysisSnapshot):
            raise TypeError("analysis store must return StoredAnalysisSnapshot values")
        repository_id = _stored_repository_id(stored_snapshot)
        metadata = metadata_by_id.get(repository_id)
        if metadata is None:
            continue
        projection = project_public_snapshot(metadata, stored_snapshot)
        if projection is None:
            continue
        key = _projection_key(projection)
        if key in keys:
            raise ValueError("analysis store contains duplicate repository/methodology snapshots")
        keys.add(key)
        projections.append(projection)
    return tuple(projections)


def _stored_repository_id(stored_snapshot: StoredAnalysisSnapshot) -> str:
    repository = stored_snapshot.snapshot.report.get("repository")
    if not isinstance(repository, dict):
        raise TypeError("stored snapshot repository must be an object")
    identifier = repository.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("stored snapshot repository id must be a nonblank string")
    return identifier.strip()


def _projection_key(projection: LeaderboardSnapshotProjection) -> tuple[str, str]:
    return projection.repository.repository_id, projection.methodology_version


def _preliminary_rows(
    projections: Iterable[LeaderboardSnapshotProjection],
    filters: LeaderboardFilters,
    sort: LeaderboardSort,
) -> tuple[LeaderboardPageRow, ...]:
    filtered = tuple(
        projection
        for projection in projections
        if projection.candidate is None and _matches(projection, filters)
    )
    return tuple(
        LeaderboardPageRow(projection=projection, rank=None)
        for projection in sorted(filtered, key=lambda item: _preliminary_sort_key(item, sort))
    )


def _preliminary_sort_key(
    projection: LeaderboardSnapshotProjection,
    sort: LeaderboardSort,
) -> tuple[object, ...]:
    if sort is LeaderboardSort.SCORE:
        return (
            projection.score is None,
            -projection.score if projection.score is not None else 0,
            projection.repository.repository_id,
        )
    if sort is LeaderboardSort.LIKES:
        likes = projection.repository.likes
        return (
            likes is None,
            -(likes or 0),
            projection.repository.repository_id,
        )
    activity = projection.repository.last_activity_at
    return (
        activity is None,
        -activity.timestamp() if activity is not None else 0,
        projection.repository.repository_id,
    )



def _matches(
    projection: LeaderboardSnapshotProjection,
    filters: LeaderboardFilters,
) -> bool:
    language = projection.repository.language
    if filters.language is not None and (
        language is None or language.casefold() != filters.language.casefold()
    ):
        return False
    return (
        filters.search is None or filters.search.casefold() in projection.repository.name.casefold()
    )


def _language_facets(
    projections: Iterable[LeaderboardSnapshotProjection],
    search: str | None,
) -> tuple[LeaderboardLanguageFacet, ...]:
    counts = Counter(
        projection.repository.language
        for projection in projections
        if projection.repository.language is not None
        and (search is None or search.casefold() in projection.repository.name.casefold())
    )
    return tuple(
        LeaderboardLanguageFacet(name=name, count=count)
        for name, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0].casefold(), item[0]),
        )
    )


def _normalize_methodology_version(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("methodology_version must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("methodology_version must not be blank")
    return normalized


def _normalize_slug(value: str) -> str | None:
    """Нормализует path-параметр, не превращая некорректный URL в ошибку API."""

    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized or None


def _normalize_filters(filters: LeaderboardFilters | None) -> LeaderboardFilters:
    if filters is None:
        return LeaderboardFilters()
    if not isinstance(filters, LeaderboardFilters):
        raise TypeError("filters must be a LeaderboardFilters or None")
    return filters


def _validate_pagination(page: int, page_size: int) -> None:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= _MAX_PAGE_SIZE
    ):
        raise ValueError(f"page_size must be between 1 and {_MAX_PAGE_SIZE}")
