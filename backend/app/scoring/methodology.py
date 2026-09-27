"""Публичное описание неизменяемых правил методики Repo Health Score v2.

v2 добавляет в Activity метрику регулярных недель коммитов. Это изменяет
формулу категории, поэтому снимки v1 и v2 нельзя сравнивать в одном рейтинге.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from backend.app.contracts import DataStatus

METHODOLOGY_VERSION = "v2"
SECURITY_OPEN_CRITICAL_METRIC = "appsec_confirmed_open_critical_findings"
SECURITY_OPEN_CRITICAL_LIMIT_CODE = "security-open-critical"
SECURITY_OPEN_CRITICAL_LIMIT = 60.0
SECURITY_OPEN_CRITICAL_LIMIT_SUMMARY = (
    "Подтверждённая открытая критическая AppSec-уязвимость ограничивает Score."
)
SECURITY_ACTIVE_STATUSES = frozenset({"OPEN", "TRIAGED_TP"})
SECURITY_CONFIRMED_OPEN_STATUSES = frozenset({"TRIAGED_TP"})
SECURITY_SEVERITY_RULES = (
    ("CRITICAL", 60, 2),
    ("HIGH", 15, 3),
    ("MEDIUM", 5, 4),
    ("LOW", 1, 10),
    ("INFO", 0, 0),
)


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
                "code": SECURITY_OPEN_CRITICAL_LIMIT_CODE,
                "maximumScore": SECURITY_OPEN_CRITICAL_LIMIT,
                "summary": SECURITY_OPEN_CRITICAL_LIMIT_SUMMARY,
            }
        ],
        "security": {
            "code": "appsec_severity_status_v1",
            "formula": (
                "100 - min(100, sum(penaltyPerFinding * min(openFindings, maximumFindings)))"
            ),
            "summary": (
                "Security Score учитывает только открытые findings из полных "
                "обезличенных результатов SAST, SCA и secret scanning."
            ),
            "activeStatuses": sorted(SECURITY_ACTIVE_STATUSES),
            "confirmedOpenCriticalStatuses": sorted(SECURITY_CONFIRMED_OPEN_STATUSES),
            "severityPenalties": [
                {
                    "severity": severity,
                    "penaltyPerFinding": penalty,
                    "maximumFindings": maximum_findings,
                }
                for severity, penalty, maximum_findings in SECURITY_SEVERITY_RULES
            ],
            "eligibility": (
                "Все три движка должны вернуть полный результат с известными severity и status; "
                "иначе категория имеет статус insufficient_sample и не участвует в Score."
            ),
        },
    }
