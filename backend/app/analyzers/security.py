"""Нормализация данных AppSec из SourceCraft для категории безопасности."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    Evidence,
    MetricResult,
    RepositoryRef,
)

CATEGORY_CODE = "security"
EVIDENCE_SOURCE = "sourcecraft-appsec"


@dataclass(frozen=True, slots=True)
class SecurityFacts:
    """Сырой результат AppSec и сведения о том, удалось ли его получить.

    В AppSec `null` отличается от пустого списка: первое означает, что платформа
    не предоставила результат сканирования, второе — что ответ получен, но для
    оценки ещё нужна согласованная методика.
    """

    payload: dict[str, Any] | list[Any] | None
    source_error: str | None = None


SecurityFactsProvider = Callable[[RepositoryRef], SecurityFacts]


def appsec_payload_status(payload: dict[str, Any] | list[Any] | None) -> DataStatus:
    """Преобразует ответ SourceCraft в честный статус доступности AppSec.

    SourceCraft вернул ``null`` для репозитория без доступных AppSec-результатов.
    Это не равно пустому списку findings: ``null`` означает, что источник
    недоступен, а список (в том числе пустой) — что ответ был получен.
    """

    if payload is None:
        return DataStatus.UNAVAILABLE
    return DataStatus.MEASURED


def build_facts(
    payload: dict[str, Any] | list[Any] | None,
    *,
    source_error: str | None = None,
) -> SecurityFacts:
    """Создаёт факты, не смешивая сетевую ошибку с отсутствием данных."""

    if source_error is not None and not source_error.strip():
        raise ValueError("source_error must not be blank")
    if source_error is not None and payload is not None:
        raise ValueError("source_error and payload cannot be provided together")
    return SecurityFacts(payload=payload, source_error=source_error)


def evaluate(facts: SecurityFacts) -> CategoryResult:
    """Возвращает честный результат категории без неподтверждённой оценки.

    Числовая формула для SAST, SCA и secret-scanning ещё не согласована. Поэтому
    даже успешный ответ не превращается самовольно в балл безопасности: до
    появления методики он остаётся `insufficient_sample`.
    """

    if facts.source_error is not None:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.ERROR,
            score=None,
            summary="Не удалось получить данные AppSec.",
            reason="appsec_source_error",
            metrics=(_availability_metric("error", "Запрос результатов AppSec завершился ошибкой."),),
            recommendations=(),
        )

    if appsec_payload_status(facts.payload) is DataStatus.UNAVAILABLE:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.UNAVAILABLE,
            score=None,
            summary="SourceCraft не предоставил результаты AppSec-сканирования.",
            reason="appsec_unavailable",
            metrics=(
                _availability_metric(
                    "unavailable",
                    "SourceCraft вернул отсутствие результатов AppSec.",
                ),
            ),
            recommendations=(),
        )

    return CategoryResult(
        category=CATEGORY_CODE,
        status=DataStatus.INSUFFICIENT_SAMPLE,
        score=None,
        summary="Результат AppSec получен, но методика оценки безопасности ещё не настроена.",
        reason="security_scoring_not_configured",
        metrics=(_availability_metric("received", "SourceCraft предоставил результат AppSec."),),
        recommendations=(),
    )


def make_analyzer(facts_provider: SecurityFactsProvider) -> Callable[[AnalysisContext], CategoryResult]:
    """Связывает будущий AppSec-поставщик с чистой функцией оценки.

    Поставщик будет добавлен после появления поддерживаемого API-контракта
    SourceCraft. Такая граница не привязывает backend к установленному у
    пользователя CLI и делает оценку полностью тестируемой.
    """

    def analyze(context: AnalysisContext) -> CategoryResult:
        return evaluate(facts_provider(context.repository))

    return analyze


def _availability_metric(value: str, summary: str) -> MetricResult:
    """Создаёт метрику доступности без содержимого findings и потенциальных секретов."""

    return MetricResult(
        code="appsec_data_availability",
        value=value,
        normalized_score=None,
        summary=summary,
        evidence=(
            Evidence(
                source=EVIDENCE_SOURCE,
                reference="appsec-defects",
                summary=summary,
            ),
        ),
    )
