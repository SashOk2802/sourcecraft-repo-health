"""Модуль расчёта итоговой оценки Repo Health Score."""

from backend.app.scoring.engine import (
    CategoryContribution,
    ScoreLimit,
    ScoreSummary,
    calculate_score,
)
from backend.app.scoring.methodology import (
    CATEGORY_LABELS,
    CATEGORY_WEIGHTS,
    METHODOLOGY_VERSION,
    build_methodology_payload,
)

__all__ = [
    "CATEGORY_LABELS",
    "CATEGORY_WEIGHTS",
    "METHODOLOGY_VERSION",
    "CategoryContribution",
    "ScoreLimit",
    "ScoreSummary",
    "build_methodology_payload",
    "calculate_score",
]
