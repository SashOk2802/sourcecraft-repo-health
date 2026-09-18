"""Модуль расчёта итоговой оценки Repo Health Score."""

from backend.app.scoring.engine import (
    CATEGORY_WEIGHTS,
    CategoryContribution,
    ScoreLimit,
    ScoreSummary,
    calculate_score,
)

__all__ = [
    "CATEGORY_WEIGHTS",
    "CategoryContribution",
    "ScoreLimit",
    "ScoreSummary",
    "calculate_score",
]
