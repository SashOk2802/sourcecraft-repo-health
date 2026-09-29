"""Общие контракты между анализаторами категорий и ядром анализа."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class DataStatus(StrEnum):
    """Доступность и качество данных, используемых категорией."""

    MEASURED = "measured"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    ERROR = "error"


class RecommendationPriority(StrEnum):
    """Порядок приоритетов рекомендаций в отчёте по репозиторию."""

    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    """Стабильная идентификация репозитория SourceCraft в одном запуске анализа."""

    id: str
    organization_slug: str
    repository_slug: str
    web_url: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """Общие входные данные, передаваемые каждому анализатору категории."""

    repository: RepositoryRef
    commit_sha: str
    analyzed_at: datetime
    period_start: datetime
    period_end: datetime


@dataclass(frozen=True, slots=True)
class Evidence:
    """Минимальный факт, подтверждающий метрику или рекомендацию."""

    source: str
    reference: str
    summary: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class MetricResult:
    """Нормализованная метрика и исходные значения для её объяснения."""

    code: str
    value: float | int | str | None
    normalized_score: float | None
    summary: str
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        if self.normalized_score is not None and not 0 <= self.normalized_score <= 100:
            raise ValueError("normalized_score must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class Recommendation:
    """Детерминированная рекомендация, сформированная по собранным фактам."""

    code: str
    priority: RecommendationPriority
    problem: str
    action: str
    rationale: str
    expected_effect: str | None = None
    expected_score_delta: float | None = None
    evidence: tuple[Evidence, ...] = ()
    ai_action_plan: str | None = None

    def __post_init__(self) -> None:
        if self.expected_score_delta is not None and not 0 <= self.expected_score_delta <= 100:
            raise ValueError("expected_score_delta must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class CategoryResult:
    """Единый результат одной из шести категорий здоровья репозитория."""

    category: str
    status: DataStatus
    score: float | None
    summary: str
    metrics: tuple[MetricResult, ...] = ()
    recommendations: tuple[Recommendation, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.score is not None and not 0 <= self.score <= 100:
            raise ValueError("category score must be between 0 and 100")
        if self.status is DataStatus.MEASURED and self.score is None:
            raise ValueError("a measured category requires a score")
        if self.status is not DataStatus.MEASURED and self.score is not None:
            raise ValueError("only a measured category may have a score")


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Сводный результат, который возвращает слой оркестрации."""

    repository: RepositoryRef
    analyzed_at: datetime
    commit_sha: str
    categories: tuple[CategoryResult, ...]
    score: float | None
    methodology_version: str
    recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)
