"""Shared contracts between category analyzers and the analysis core."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class DataStatus(StrEnum):
    """Availability and quality of data used by a category."""

    MEASURED = "measured"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    ERROR = "error"


class RecommendationPriority(StrEnum):
    """Priority order used in repository reports."""

    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    """Stable SourceCraft repository identity for an analysis run."""

    id: str
    organization_slug: str
    repository_slug: str
    web_url: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """Shared input passed to every category analyzer."""

    repository: RepositoryRef
    commit_sha: str
    analyzed_at: datetime
    period_start: datetime
    period_end: datetime


@dataclass(frozen=True, slots=True)
class Evidence:
    """A minimal fact supporting a metric or recommendation."""

    source: str
    reference: str
    summary: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class MetricResult:
    """A normalized metric and the raw values needed to explain it."""

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
    """A deterministic recommendation formed from collected evidence."""

    code: str
    priority: RecommendationPriority
    problem: str
    action: str
    rationale: str
    expected_effect: str | None = None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class CategoryResult:
    """Common output of one of the six repository-health categories."""

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
    """Aggregate result returned by the orchestration layer."""

    repository: RepositoryRef
    analyzed_at: datetime
    categories: tuple[CategoryResult, ...]
    score: float | None
    methodology_version: str
    recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)

