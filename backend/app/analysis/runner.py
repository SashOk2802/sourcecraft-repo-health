"""Оркестрация независимых анализаторов в единый результат анализа."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from backend.app.contracts import (
    AnalysisContext,
    AnalysisResult,
    CategoryResult,
    DataStatus,
    Recommendation,
    RecommendationPriority,
)
from backend.app.scoring.engine import (
    CATEGORY_WEIGHTS,
    METHODOLOGY_VERSION,
    ScoreLimit,
    ScoreSummary,
    calculate_score,
)

logger = logging.getLogger(__name__)

CategoryEvaluator = Callable[[AnalysisContext], CategoryResult]

_PRIORITY_ORDER = {
    RecommendationPriority.P0: 0,
    RecommendationPriority.P1: 1,
    RecommendationPriority.P2: 2,
    RecommendationPriority.P3: 3,
}


@dataclass(frozen=True, slots=True)
class AnalyzerRegistration:
    """Анализатор категории и его стабильный код в методике v1."""

    category: str
    evaluate: CategoryEvaluator


@dataclass(frozen=True, slots=True)
class AnalysisExecution:
    """Общий результат запуска: модель анализа и детализация расчёта Score."""

    analysis: AnalysisResult
    score_summary: ScoreSummary


def run_analysis(
    context: AnalysisContext,
    analyzers: Iterable[AnalyzerRegistration],
    *,
    score_limit: ScoreLimit | None = None,
) -> AnalysisExecution:
    """Выполняет анализаторы независимо и собирает итог из шести категорий."""

    registrations = _validate_registrations(analyzers)
    categories = tuple(
        _run_category(context, code, registrations.get(code))
        for code in CATEGORY_WEIGHTS
    )
    score_summary = calculate_score(categories, score_limit=score_limit)
    recommendations = _merge_recommendations(categories)

    return AnalysisExecution(
        analysis=AnalysisResult(
            repository=context.repository,
            analyzed_at=context.analyzed_at,
            commit_sha=context.commit_sha,
            categories=categories,
            score=score_summary.score,
            methodology_version=METHODOLOGY_VERSION,
            recommendations=recommendations,
        ),
        score_summary=score_summary,
    )


def _validate_registrations(
    analyzers: Iterable[AnalyzerRegistration],
) -> dict[str, AnalyzerRegistration]:
    registrations = tuple(analyzers)
    categories = tuple(registration.category for registration in registrations)
    duplicate_categories = sorted({category for category in categories if categories.count(category) > 1})
    unexpected_categories = sorted(set(categories) - set(CATEGORY_WEIGHTS))

    if duplicate_categories:
        raise ValueError(f"duplicate analyzer categories: {', '.join(duplicate_categories)}")
    if unexpected_categories:
        raise ValueError(f"unexpected analyzer categories: {', '.join(unexpected_categories)}")

    return {registration.category: registration for registration in registrations}


def _run_category(
    context: AnalysisContext,
    category: str,
    registration: AnalyzerRegistration | None,
) -> CategoryResult:
    if registration is None:
        return CategoryResult(
            category=category,
            status=DataStatus.UNAVAILABLE,
            score=None,
            summary="Анализатор категории пока не подключён.",
            reason="analyzer_not_configured",
        )

    try:
        result = registration.evaluate(context)
    except Exception:
        # Ошибка одного анализатора не должна отменять сбор остальных категорий.
        logger.exception(
            "Ошибка выполнения анализатора.",
            extra={"category": category, "repository_id": context.repository.id},
        )
        return CategoryResult(
            category=category,
            status=DataStatus.ERROR,
            score=None,
            summary="Не удалось рассчитать категорию.",
            reason="analyzer_execution_failed",
        )

    if result.category != category:
        return CategoryResult(
            category=category,
            status=DataStatus.ERROR,
            score=None,
            summary="Анализатор вернул результат другой категории.",
            reason="analyzer_category_mismatch",
        )

    return result


def _merge_recommendations(categories: Iterable[CategoryResult]) -> tuple[Recommendation, ...]:
    recommendations_by_code: dict[str, Recommendation] = {}

    for category in categories:
        for recommendation in category.recommendations:
            previous = recommendations_by_code.get(recommendation.code)
            if previous is None or _priority(recommendation) < _priority(previous):
                recommendations_by_code[recommendation.code] = recommendation

    return tuple(
        sorted(
            recommendations_by_code.values(),
            key=lambda recommendation: (_priority(recommendation), recommendation.code),
        )
    )


def _priority(recommendation: Recommendation) -> int:
    return _PRIORITY_ORDER[recommendation.priority]
