"""Публичное описание неизменяемых правил методики Repo Health Score.

Текущая версия — v2. От v1 она отличается формулой Activity: метрика
``active_weeks_in_period`` с сырым весом 25 меняет оценку категории.
Снимки v1 и v2 в одном рейтинге не сравниваются.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from backend.app.contracts import DataStatus

METHODOLOGY_VERSION = "v2"


@dataclass(frozen=True, slots=True)
class MethodologyCategory:
    """Категория Score с названием, показываемым в отчётах и интерфейсе."""

    code: str
    label: str
    weight: float


CATEGORIES = (
    MethodologyCategory("security", "Безопасность", 25.0),
    MethodologyCategory("cicd", "CI/CD", 20.0),
    MethodologyCategory("documentation", "Документация", 20.0),
    MethodologyCategory("activity", "Активность", 15.0),
    MethodologyCategory("issues", "Работа с issues", 15.0),
    MethodologyCategory("code_health", "Состояние кода", 5.0),
)

CATEGORY_WEIGHTS = MappingProxyType({category.code: category.weight for category in CATEGORIES})
CATEGORY_LABELS = MappingProxyType({category.code: category.label for category in CATEGORIES})

_STATUS_DESCRIPTIONS = MappingProxyType(
    {
        DataStatus.MEASURED: "Данные получены, категория участвует в Score.",
        DataStatus.UNAVAILABLE: "Источник не предоставил данные; это не нулевая оценка.",
        DataStatus.NOT_APPLICABLE: "Категория неприменима и исключена из Score и Coverage.",
        DataStatus.INSUFFICIENT_SAMPLE: "Данные есть, но выборки недостаточно для честной оценки.",
        DataStatus.ERROR: "Во время анализа произошла ошибка; категория не участвует в Score.",
    }
)


def build_methodology_payload() -> dict[str, object]:
    """Строит публичный JSON для страницы «Как считаем» из правил ядра."""

    return {
        "version": METHODOLOGY_VERSION,
        "scoreRange": {"minimum": 0, "maximum": 100},
        "categories": [
            {"code": category.code, "label": category.label, "weight": category.weight}
            for category in CATEGORIES
        ],
        "aggregation": {
            "code": "weighted_average_of_measured_categories",
            "formula": "sum(categoryScore * weight) / sum(weight)",
            "summary": "Итог — средневзвешенная оценка только по измеренным категориям.",
            "coverageFormula": "measuredWeight / applicableWeight",
            "coverageSummary": (
                "Coverage показывает долю измеренного применимого веса и не уменьшает Score."
            ),
            "preliminaryRule": "Coverage меньше 1 означает предварительную оценку.",
        },
        "dataStatuses": [
            {"code": status.value, "summary": _STATUS_DESCRIPTIONS[status]}
            for status in DataStatus
        ],
        "scoreLimits": [
            {
                "code": "security-open-critical",
                "maximumScore": 60,
                "summary": (
                    "Подтверждённая открытая критическая AppSec-уязвимость ограничивает Score."
                ),
            }
        ],
    }
