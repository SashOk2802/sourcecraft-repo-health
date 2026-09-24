"""Нормализация данных AppSec из SourceCraft для категории безопасности."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
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
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SecurityFacts:
    """Сырой результат AppSec и сведения о том, удалось ли его получить.

    В AppSec `null` отличается от пустого списка: первое означает, что платформа
    не предоставила результат сканирования, второе — что ответ получен, но для
    оценки ещё нужна согласованная методика.
    """

    # Содержимое AppSec и ошибок может включать секреты: исключаем его из repr.
    payload: dict[str, Any] | list[Any] | None = field(repr=False)
    source_error: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _validate_payload(self.payload)
        if self.source_error is not None:
            if not isinstance(self.source_error, str):
                raise TypeError("source_error must be a string or None")
            if not self.source_error.strip():
                raise ValueError("source_error must not be blank")
            if self.payload is not None:
                raise ValueError("source_error and payload cannot be provided together")


SecurityFactsProvider = Callable[[RepositoryRef], SecurityFacts]


def appsec_payload_status(payload: dict[str, Any] | list[Any] | None) -> DataStatus:
    """Преобразует ответ SourceCraft в честный статус доступности AppSec.

    SourceCraft вернул ``null`` для репозитория без доступных AppSec-результатов.
    Это не равно пустому списку findings: ``null`` означает отсутствие результата,
    но не объясняет причину. Список или объект подтверждает только получение
    ответа, а не успешное сканирование или возможность рассчитать score.
    """

    _validate_payload(payload)
    if payload is None:
        return DataStatus.UNAVAILABLE
    return DataStatus.MEASURED


def build_facts(
    payload: dict[str, Any] | list[Any] | None,
    *,
    source_error: str | None = None,
) -> SecurityFacts:
    """Создаёт факты, не смешивая сетевую ошибку с отсутствием данных."""

    return SecurityFacts(payload=payload, source_error=source_error)


def evaluate(facts: SecurityFacts) -> CategoryResult:
    """Возвращает честный результат категории без неподтверждённой оценки.

    Числовая формула для SAST, SCA и secret-scanning ещё не согласована. Поэтому
    даже непустой ответ не превращается самовольно в балл безопасности: до
    появления методики он остаётся `insufficient_sample`.
    """

    if facts.source_error is not None:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.ERROR,
            score=None,
            summary="Не удалось получить данные AppSec.",
            reason="appsec_source_error",
            metrics=(
                _availability_metric("error", "Запрос результатов AppSec завершился ошибкой."),
            ),
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


def make_analyzer(
    facts_provider: SecurityFactsProvider,
) -> Callable[[AnalysisContext], CategoryResult]:
    """Связывает будущий AppSec-поставщик с чистой функцией оценки.

    Поставщик отвечает за получение и проверку ответа по контракту SourceCraft;
    его сетевое подключение в этот модуль не входит. Исключение на этой границе
    превращается в error без записи его содержимого в журнал общего runner.
    """

    def analyze(context: AnalysisContext) -> CategoryResult:
        try:
            facts = facts_provider(context.repository)
            if not isinstance(facts, SecurityFacts):
                raise TypeError("AppSec provider must return SecurityFacts")
        except Exception:  # noqa: BLE001 — граница поставщика, сырые исключения не должны утекать.
            # Не логируем исключение/traceback: в них могут быть ответ AppSec и токен.
            # BaseException (остановка процесса и отмена) сюда не попадает.
            logger.warning("Не удалось получить корректные данные AppSec.")
            facts = build_facts(None, source_error="appsec_provider_failed")
        return evaluate(facts)

    return analyze


def _validate_payload(payload: object) -> None:
    # Проверяется только верхний уровень: схема findings пока не подтверждена.
    if payload is not None and not isinstance(payload, dict | list):
        raise TypeError("AppSec payload must be an object, an array or None")


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
