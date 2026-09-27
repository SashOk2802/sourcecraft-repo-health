"""Безопасная проекция сохранённого публичного отчёта в строку рейтинга."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from urllib.parse import urlparse

from backend.app.analysis import StoredAnalysisSnapshot
from backend.app.leaderboard.policy import LeaderboardCandidate

_CATEGORY_STATUSES = frozenset(
    {
        "measured",
        "unavailable",
        "not_applicable",
        "insufficient_sample",
        "error",
    }
)


@dataclass(frozen=True, slots=True)
class PublicRepositoryMetadata:
    """Проверенные публичные поля репозитория из каталога SourceCraft."""

    repository_id: str
    organization_slug: str
    repository_slug: str
    url: str | None
    description: str | None = None
    language: str | None = None
    likes: int | None = None
    last_activity_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("repository_id", self.repository_id),
            ("organization_slug", self.organization_slug),
            ("repository_slug", self.repository_slug),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a nonblank string")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(self, "url", _normalize_optional_url(self.url))
        for field_name in ("description", "language"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be a nonblank string or None")
            if isinstance(value, str):
                object.__setattr__(self, field_name, value.strip())
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
class LeaderboardCategoryBrief:
    """Краткая категория для полосы Score в одной строке рейтинга."""

    code: str
    label: str
    status: str
    score: float | None


@dataclass(frozen=True, slots=True)
class LeaderboardSnapshotProjection:
    """Строка рейтинга, построенная только из проверенного public-снимка."""

    analysis_id: str
    repository: PublicRepositoryMetadata
    methodology_version: str
    score: float | None
    is_preliminary: bool
    candidate: LeaderboardCandidate | None
    analyzed_at: datetime
    coverage: float | None
    score_limited: bool
    categories: tuple[LeaderboardCategoryBrief, ...]


def project_public_snapshot(
    metadata: PublicRepositoryMetadata,
    stored_snapshot: StoredAnalysisSnapshot,
) -> LeaderboardSnapshotProjection | None:
    """Возвращает строку public-рейтинга либо исключает неподходящий снимок.

    Запись исключается, если снимок относится к другому репозиторию. Снимок
    без итогового Score сохраняется для предварительного блока без места. Так
    private/stale отчёт не сможет попасть в результат лишь из-за совпавшего
    идентификатора. Повреждённый формат сохранённого отчёта считается ошибкой
    хранилища и завершается ValueError.
    """

    if not isinstance(metadata, PublicRepositoryMetadata):
        raise TypeError("metadata must be PublicRepositoryMetadata")
    if not isinstance(stored_snapshot, StoredAnalysisSnapshot):
        raise TypeError("stored_snapshot must be StoredAnalysisSnapshot")

    report = stored_snapshot.snapshot.report
    repository = _required_object(report, "repository")
    if not _matches_public_repository(metadata, repository):
        return None

    analysis = _required_object(report, "analysis")
    if _required_string(analysis, "id") != stored_snapshot.analysis_id:
        raise ValueError("stored snapshot analysis id does not match its key")

    score = _optional_score(report.get("score"), field_name="score")
    methodology_version = _required_string(analysis, "methodologyVersion")
    is_preliminary = _required_bool(analysis, "isPreliminary")
    status = _required_string(analysis, "status")
    expected_status = "partial" if is_preliminary else "completed"
    if status != expected_status:
        raise ValueError("stored snapshot analysis status is invalid")

    return LeaderboardSnapshotProjection(
        analysis_id=stored_snapshot.analysis_id,
        repository=metadata,
        methodology_version=methodology_version,
        score=score,
        is_preliminary=is_preliminary,
        candidate=(
            LeaderboardCandidate(
                repository_id=metadata.repository_id,
                organization_slug=metadata.organization_slug,
                repository_slug=metadata.repository_slug,
                methodology_version=methodology_version,
                score=score,
                is_preliminary=is_preliminary,
                language=metadata.language,
                likes=metadata.likes,
                last_activity_at=metadata.last_activity_at,
            )
            if score is not None
            else None
        ),
        analyzed_at=_required_timestamp(analysis, "analyzedAt"),
        coverage=_optional_coverage(analysis),
        score_limited=analysis.get("scoreLimit") is not None,
        categories=_category_briefs(report),
    )


def _matches_public_repository(
    metadata: PublicRepositoryMetadata,
    repository: dict[str, object],
) -> bool:
    return (
        repository.get("id") == metadata.repository_id
        and repository.get("organizationSlug") == metadata.organization_slug
        and repository.get("repositorySlug") == metadata.repository_slug
    )


def _category_briefs(report: dict[str, object]) -> tuple[LeaderboardCategoryBrief, ...]:
    categories = report.get("categories")
    if not isinstance(categories, list):
        raise TypeError("stored snapshot categories must be a list")

    result: list[LeaderboardCategoryBrief] = []
    codes: set[str] = set()
    for category in categories:
        if not isinstance(category, dict):
            raise TypeError("stored snapshot category must be an object")
        code = _required_string(category, "code")
        if code in codes:
            raise ValueError("stored snapshot category codes must be unique")
        codes.add(code)
        status = _required_string(category, "status")
        if status not in _CATEGORY_STATUSES:
            raise ValueError("stored snapshot category status is invalid")
        score = _optional_score(category.get("score"), field_name="category score")
        if (status == "measured") != (score is not None):
            raise ValueError("stored snapshot category score does not match its status")
        result.append(
            LeaderboardCategoryBrief(
                code=code,
                label=_required_string(category, "label"),
                status=status,
                score=score,
            )
        )
    return tuple(result)


def _optional_coverage(analysis: dict[str, object]) -> float | None:
    coverage = analysis.get("coverage")
    if coverage is None:
        return None
    if isinstance(coverage, bool) or not isinstance(coverage, int | float):
        raise TypeError("stored snapshot coverage must be a number or null")
    numeric_coverage = float(coverage)
    if not isfinite(numeric_coverage) or not 0 <= numeric_coverage <= 1:
        raise ValueError("stored snapshot coverage must be between 0 and 1")
    return numeric_coverage


def _required_timestamp(container: dict[str, object], field: str) -> datetime:
    value = _required_string(container, field)
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"stored snapshot {field} must be an ISO timestamp") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"stored snapshot {field} must include a timezone")
    return timestamp.astimezone(UTC)


def _optional_score(value: object, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"stored snapshot {field_name} must be a number or null")
    score = float(value)
    if not isfinite(score) or not 0 <= score <= 100:
        raise ValueError(f"stored snapshot {field_name} must be between 0 and 100")
    return score


def _required_object(container: dict[str, object], field: str) -> dict[str, object]:
    value = container.get(field)
    if not isinstance(value, dict):
        raise TypeError(f"stored snapshot {field} must be an object")
    return value


def _required_string(container: dict[str, object], field: str) -> str:
    value = container.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"stored snapshot {field} must be a nonblank string")
    return value.strip()


def _required_bool(container: dict[str, object], field: str) -> bool:
    value = container.get(field)
    if not isinstance(value, bool):
        raise TypeError(f"stored snapshot {field} must be a bool")
    return value


def _normalize_optional_url(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("url must be a nonblank HTTPS URL or None")
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("url must be a nonblank HTTPS URL or None")
    return normalized
