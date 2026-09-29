"""Детерминированные правила выборки и ранжирования публичных отчётов."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite


class LeaderboardSort(StrEnum):
    """Порядок строк в интерфейсе без изменения места репозитория."""

    SCORE = "score"
    LIKES = "likes"
    ACTIVITY = "activity"


@dataclass(frozen=True, slots=True)
class LeaderboardCandidate:
    """Безопасная публичная проекция одного уже опубликованного анализа."""

    repository_id: str
    organization_slug: str
    repository_slug: str
    methodology_version: str
    score: float
    is_preliminary: bool
    language: str | None
    likes: int | None
    last_activity_at: datetime | None

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.repository_id, self.organization_slug, self.repository_slug)
        ):
            raise ValueError("repository identity fields must not be blank")
        if not isinstance(self.methodology_version, str) or not self.methodology_version.strip():
            raise ValueError("methodology_version must be a nonblank string")
        object.__setattr__(self, "methodology_version", self.methodology_version.strip())
        if not isinstance(self.is_preliminary, bool):
            raise TypeError("is_preliminary must be a bool")
        if isinstance(self.score, bool) or not isinstance(self.score, int | float):
            raise TypeError("score must be a number")
        if not isfinite(self.score) or not 0 <= self.score <= 100:
            raise ValueError("score must be between 0 and 100")
        if self.language is not None:
            if not isinstance(self.language, str) or not self.language.strip():
                raise ValueError("language must be a nonblank string or None")
            object.__setattr__(self, "language", self.language.strip())
        if self.likes is not None and (
            isinstance(self.likes, bool) or not isinstance(self.likes, int) or self.likes < 0
        ):
            raise ValueError("likes must be a non-negative integer or None")
        if self.last_activity_at is not None and (
            not isinstance(self.last_activity_at, datetime)
            or self.last_activity_at.tzinfo is None
            or self.last_activity_at.utcoffset() is None
        ):
            raise ValueError("last_activity_at must be timezone-aware or None")

    @property
    def name(self) -> str:
        """Возвращает отображаемое имя репозитория."""

        return f"{self.organization_slug}/{self.repository_slug}"


@dataclass(frozen=True, slots=True)
class LeaderboardFilters:
    """Фильтры интерфейса, не влияющие на сам расчёт места."""

    language: str | None = None
    search: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "language", _normalize_optional_filter(self.language, "language"))
        object.__setattr__(self, "search", _normalize_optional_filter(self.search, "search"))


@dataclass(frozen=True, slots=True)
class LeaderboardRow:
    """Строка рейтинга с неизменяемым местом."""

    candidate: LeaderboardCandidate
    rank: int | None


@dataclass(frozen=True, slots=True)
class LeaderboardResult:
    """Результаты одной версии методики и число частичных оценок среди них."""

    methodology_version: str
    entries: tuple[LeaderboardRow, ...]
    total: int
    partial_total: int


def build_leaderboard(
    candidates: Iterable[LeaderboardCandidate],
    *,
    methodology_version: str,
    filters: LeaderboardFilters | None = None,
    sort: LeaderboardSort = LeaderboardSort.SCORE,
) -> LeaderboardResult:
    """Строит рейтинг одной версии методики.

    Переданная версия выбирается до расчёта места: Score разных методик нельзя
    смешивать в одном сравнении. Место рассчитывается по Score среди всех
    доступных публичных записей этой версии до применения фильтров и
    UI-сортировки. Частичная оценка с числовым Score тоже получает место, но
    остаётся явно помеченной как preliminary. Поэтому сортировка по лайкам или
    активности переставляет строки, но не меняет места. Равные Score получают
    общее спортивное место: 1, 2, 2, 4.
    """

    if not isinstance(sort, LeaderboardSort):
        raise TypeError("sort must be a LeaderboardSort")
    selected_methodology_version = _normalize_methodology_version(methodology_version)

    all_candidates = tuple(candidates)
    _validate_candidates(all_candidates)
    if filters is not None and not isinstance(filters, LeaderboardFilters):
        raise TypeError("filters must be a LeaderboardFilters or None")
    effective_filters = filters or LeaderboardFilters()
    version_candidates = tuple(
        candidate
        for candidate in all_candidates
        if candidate.methodology_version == selected_methodology_version
    )

    ranked_rows = _rank_candidates(version_candidates)
    visible_rows = tuple(row for row in ranked_rows if _matches(row.candidate, effective_filters))
    ordered_rows = _sort_rows(visible_rows, sort)

    return LeaderboardResult(
        methodology_version=selected_methodology_version,
        entries=ordered_rows,
        total=len(ordered_rows),
        partial_total=sum(row.candidate.is_preliminary for row in ordered_rows),
    )


def _rank_candidates(
    candidates: tuple[LeaderboardCandidate, ...],
) -> tuple[LeaderboardRow, ...]:
    ordered = sorted(
        candidates,
        key=lambda candidate: (-candidate.score, candidate.repository_id),
    )
    rows: list[LeaderboardRow] = []
    previous_score: float | None = None
    current_rank = 0
    for index, candidate in enumerate(ordered, start=1):
        if previous_score is None or candidate.score != previous_score:
            current_rank = index
            previous_score = candidate.score
        rows.append(LeaderboardRow(candidate=candidate, rank=current_rank))
    return tuple(rows)


def _sort_rows(
    rows: tuple[LeaderboardRow, ...],
    sort: LeaderboardSort,
) -> tuple[LeaderboardRow, ...]:
    if sort is LeaderboardSort.SCORE:
        return tuple(
            sorted(
                rows,
                key=lambda row: (-row.candidate.score, row.candidate.repository_id),
            )
        )
    if sort is LeaderboardSort.LIKES:
        return tuple(
            sorted(
                rows,
                key=lambda row: (
                    row.candidate.likes is None,
                    -(row.candidate.likes or 0),
                    row.candidate.repository_id,
                ),
            )
        )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.candidate.last_activity_at is None,
                -row.candidate.last_activity_at.timestamp()
                if row.candidate.last_activity_at is not None
                else 0,
                row.candidate.repository_id,
            ),
        )
    )


def _matches(candidate: LeaderboardCandidate, filters: LeaderboardFilters) -> bool:
    if (
        filters.language is not None
        and (candidate.language is None or candidate.language.casefold() != filters.language.casefold())
    ):
        return False
    return filters.search is None or filters.search.casefold() in candidate.name.casefold()


def _validate_candidates(candidates: tuple[LeaderboardCandidate, ...]) -> None:
    if not all(isinstance(candidate, LeaderboardCandidate) for candidate in candidates):
        raise TypeError("candidates must contain LeaderboardCandidate values")
    identifiers = tuple(
        (candidate.repository_id, candidate.methodology_version) for candidate in candidates
    )
    duplicates = sorted({value for value in identifiers if identifiers.count(value) > 1})
    if duplicates:
        rendered_duplicates = ", ".join(
            f"{repository_id}@{version}" for repository_id, version in duplicates
        )
        raise ValueError(f"duplicate repository/version pairs: {rendered_duplicates}")


def _normalize_optional_filter(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    normalized = value.strip()
    return normalized or None


def _normalize_methodology_version(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("methodology_version must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("methodology_version must not be blank")
    return normalized
