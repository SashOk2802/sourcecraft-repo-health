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
    score: float
    is_preliminary: bool
    language: str | None
    likes: int
    last_activity_at: datetime | None

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.repository_id, self.organization_slug, self.repository_slug)
        ):
            raise ValueError("repository identity fields must not be blank")
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
        if isinstance(self.likes, bool) or not isinstance(self.likes, int) or self.likes < 0:
            raise ValueError("likes must be a non-negative integer")
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
    """Строка рейтинга с неизменяемым местом или без него для preliminary."""

    candidate: LeaderboardCandidate
    rank: int | None


@dataclass(frozen=True, slots=True)
class LeaderboardResult:
    """Полные и предварительные результаты одной публичной выборки."""

    entries: tuple[LeaderboardRow, ...]
    total: int
    preliminary_entries: tuple[LeaderboardRow, ...]
    preliminary_total: int


def build_leaderboard(
    candidates: Iterable[LeaderboardCandidate],
    *,
    filters: LeaderboardFilters | None = None,
    sort: LeaderboardSort = LeaderboardSort.SCORE,
    include_preliminary: bool = False,
) -> LeaderboardResult:
    """Строит рейтинг полной оценки и отдельный список preliminary-результатов.

    Место рассчитывается по Score среди всех доступных публичных полных записей
    до применения фильтров и UI-сортировки. Поэтому сортировка по лайкам или
    активности переставляет строки, но не меняет места. Равные Score получают
    общее спортивное место: 1, 2, 2, 4.
    """

    if not isinstance(sort, LeaderboardSort):
        raise TypeError("sort must be a LeaderboardSort")
    if not isinstance(include_preliminary, bool):
        raise TypeError("include_preliminary must be a bool")

    all_candidates = tuple(candidates)
    _validate_candidates(all_candidates)
    if filters is not None and not isinstance(filters, LeaderboardFilters):
        raise TypeError("filters must be a LeaderboardFilters or None")
    effective_filters = filters or LeaderboardFilters()

    full_rows = _rank_full_candidates(all_candidates)
    visible_full_rows = tuple(row for row in full_rows if _matches(row.candidate, effective_filters))
    ordered_full_rows = _sort_rows(visible_full_rows, sort)

    preliminary_rows = tuple(
        LeaderboardRow(candidate=candidate, rank=None)
        for candidate in all_candidates
        if candidate.is_preliminary and _matches(candidate, effective_filters)
    )
    ordered_preliminary_rows = _sort_rows(preliminary_rows, sort)

    return LeaderboardResult(
        entries=ordered_full_rows,
        total=len(ordered_full_rows),
        preliminary_entries=ordered_preliminary_rows if include_preliminary else (),
        preliminary_total=len(ordered_preliminary_rows),
    )


def _rank_full_candidates(
    candidates: tuple[LeaderboardCandidate, ...],
) -> tuple[LeaderboardRow, ...]:
    ordered = sorted(
        (candidate for candidate in candidates if not candidate.is_preliminary),
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
                key=lambda row: (-row.candidate.likes, row.candidate.repository_id),
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
    identifiers = tuple(candidate.repository_id for candidate in candidates)
    duplicates = sorted({value for value in identifiers if identifiers.count(value) > 1})
    if duplicates:
        raise ValueError(f"duplicate repository ids: {', '.join(duplicates)}")


def _normalize_optional_filter(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    normalized = value.strip()
    return normalized or None
