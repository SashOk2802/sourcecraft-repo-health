"""Чистый расчёт итоговой оценки Repo Health Score по результатам категорий."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import fsum, isclose
from types import MappingProxyType

from backend.app.contracts import CategoryResult, DataStatus

CATEGORY_WEIGHTS = MappingProxyType(
    {
        "security": 25.0,
        "cicd": 20.0,
        "documentation": 20.0,
        "activity": 15.0,
        "issues": 15.0,
        "code_health": 5.0,
    }
)


@dataclass(frozen=True, slots=True)
class ScoreLimit:
    """Подтверждённое ограничение итоговой оценки."""

    maximum_score: float
    code: str
    summary: str

    def __post_init__(self) -> None:
        if not 0 <= self.maximum_score <= 100:
            raise ValueError("score limit must be between 0 and 100")
        if not self.code:
            raise ValueError("score limit code must not be empty")
        if not self.summary:
            raise ValueError("score limit summary must not be empty")


@dataclass(frozen=True, slots=True)
class CategoryContribution:
    """Вклад одной категории в итог с учётом доступных данных."""

    category: str
    weight: float
    effective_weight: float | None
    points: float | None


@dataclass(frozen=True, slots=True)
class ScoreSummary:
    """Результат расчёта Score, покрытия и вкладов по всем категориям."""

    score: float | None
    uncapped_score: float | None
    coverage: float
    measured_weight: float
    applicable_weight: float
    is_preliminary: bool
    categories: tuple[CategoryContribution, ...]
    score_limit: ScoreLimit | None


def calculate_score(
    categories: Iterable[CategoryResult],
    *,
    score_limit: ScoreLimit | None = None,
) -> ScoreSummary:
    """Считает Score v1 без сети, времени и округления промежуточных значений."""

    categories_by_code = _validate_categories(categories)
    _validate_score_limit(categories_by_code, score_limit)

    applicable_weight = sum(
        weight
        for code, weight in CATEGORY_WEIGHTS.items()
        if categories_by_code[code].status is not DataStatus.NOT_APPLICABLE
    )
    measured_weight = sum(
        weight
        for code, weight in CATEGORY_WEIGHTS.items()
        if categories_by_code[code].status is DataStatus.MEASURED
    )
    coverage = measured_weight / applicable_weight if applicable_weight else 0.0

    contributions = tuple(
        _build_contribution(
            category=categories_by_code[code],
            weight=weight,
            measured_weight=measured_weight,
        )
        for code, weight in CATEGORY_WEIGHTS.items()
    )

    if not measured_weight:
        return ScoreSummary(
            score=None,
            uncapped_score=None,
            coverage=coverage,
            measured_weight=measured_weight,
            applicable_weight=applicable_weight,
            is_preliminary=True,
            categories=contributions,
            score_limit=None,
        )

    uncapped_score = _normalize_score(
        fsum(contribution.points for contribution in contributions if contribution.points is not None)
    )
    applied_limit = (
        score_limit if score_limit is not None and uncapped_score > score_limit.maximum_score else None
    )
    score = _normalize_score(
        min(uncapped_score, applied_limit.maximum_score) if applied_limit else uncapped_score
    )

    return ScoreSummary(
        score=score,
        uncapped_score=uncapped_score,
        coverage=coverage,
        measured_weight=measured_weight,
        applicable_weight=applicable_weight,
        is_preliminary=coverage < 1,
        categories=contributions,
        score_limit=applied_limit,
    )


def _build_contribution(
    *,
    category: CategoryResult,
    weight: float,
    measured_weight: float,
) -> CategoryContribution:
    if category.status is not DataStatus.MEASURED:
        return CategoryContribution(
            category=category.category,
            weight=weight,
            effective_weight=None,
            points=None,
        )

    effective_weight = weight / measured_weight * 100
    points = category.score * effective_weight / 100

    return CategoryContribution(
        category=category.category,
        weight=weight,
        effective_weight=effective_weight,
        points=points,
    )


def _normalize_score(value: float) -> float:
    """Устраняет только вычислительный шум на границах допустимого диапазона."""

    if isclose(value, 0.0, abs_tol=1e-12):
        return 0.0
    if isclose(value, 100.0, abs_tol=1e-12):
        return 100.0
    return value


def _validate_categories(categories: Iterable[CategoryResult]) -> dict[str, CategoryResult]:
    categories_list = tuple(categories)
    category_codes = tuple(category.category for category in categories_list)
    duplicate_codes = sorted({code for code in category_codes if category_codes.count(code) > 1})

    if duplicate_codes:
        raise ValueError(f"duplicate categories: {', '.join(duplicate_codes)}")

    categories_by_code = {category.category: category for category in categories_list}
    expected_codes = set(CATEGORY_WEIGHTS)
    actual_codes = set(categories_by_code)
    missing_codes = sorted(expected_codes - actual_codes)
    unexpected_codes = sorted(actual_codes - expected_codes)

    if missing_codes or unexpected_codes:
        parts: list[str] = []
        if missing_codes:
            parts.append(f"missing: {', '.join(missing_codes)}")
        if unexpected_codes:
            parts.append(f"unexpected: {', '.join(unexpected_codes)}")
        raise ValueError(f"categories must match methodology v1 ({'; '.join(parts)})")

    return categories_by_code


def _validate_score_limit(
    categories: dict[str, CategoryResult],
    score_limit: ScoreLimit | None,
) -> None:
    if score_limit is not None and categories["security"].status is not DataStatus.MEASURED:
        raise ValueError("score limit requires measured security category")
