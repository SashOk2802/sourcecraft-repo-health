"""Нормализация данных AppSec из SourceCraft для категории безопасности."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    Evidence,
    MetricResult,
    Recommendation,
    RecommendationPriority,
    RepositoryRef,
)
from backend.app.integrations.sourcecraft_appsec_probe import (
    APPSEC_ENGINES,
    APPSEC_SEVERITIES,
    APPSEC_STATUSES,
)
from backend.app.scoring.methodology import (
    SECURITY_ACTIVE_STATUSES,
    SECURITY_CONFIRMED_OPEN_STATUSES,
    SECURITY_OPEN_CRITICAL_METRIC,
    SECURITY_SEVERITY_RULES,
)

CATEGORY_CODE = "security"
EVIDENCE_SOURCE = "sourcecraft-appsec"
logger = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class _SecurityAssessment:
    """Проверенная, но обезличенная основа для Security Score v1."""

    score: float
    active_by_severity: tuple[tuple[str, int], ...]
    confirmed_open_critical: int

    @property
    def active_finding_count(self) -> int:
        return sum(count for _, count in self.active_by_severity)

    def count_for(self, severity: str) -> int:
        return dict(self.active_by_severity).get(severity, 0)


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
SecurityContextFactsProvider = Callable[[AnalysisContext], SecurityFacts]


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
    """Считает Security Score только по полному безопасному агрегату AppSec."""

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

    assessment = _build_assessment(facts.payload)
    if assessment is None:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.INSUFFICIENT_SAMPLE,
            score=None,
            summary=(
                "Результат AppSec получен, но полнота сканов или безопасная схема "
                "severity/status пока не подтверждены."
            ),
            reason="appsec_coverage_not_confirmed",
            metrics=(
                _availability_metric(
                    "received",
                    "SourceCraft предоставил результат AppSec, но он не готов для Score.",
                ),
            ),
            recommendations=(),
        )

    return CategoryResult(
        category=CATEGORY_CODE,
        status=DataStatus.MEASURED,
        score=assessment.score,
        summary=_score_summary(assessment),
        reason="security_score_v1",
        metrics=_score_metrics(assessment),
        recommendations=_recommendations(assessment),
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
        return _evaluate_provider(lambda: facts_provider(context.repository))

    return analyze


def make_context_analyzer(
    facts_provider: SecurityContextFactsProvider,
) -> Callable[[AnalysisContext], CategoryResult]:
    """Создаёт анализатор, когда источнику нужен commit текущего запуска.

    Используется bridge'ем обезличенных AppSec-сводок: файл обязан относиться
    именно к анализируемому commit, поэтому одного ``RepositoryRef`` недостаточно.
    Ошибки получают ту же безопасную границу, что и обычный поставщик.
    """

    def analyze(context: AnalysisContext) -> CategoryResult:
        return _evaluate_provider(lambda: facts_provider(context))

    return analyze


def _evaluate_provider(load_facts: Callable[[], SecurityFacts]) -> CategoryResult:
    try:
        facts = load_facts()
        if not isinstance(facts, SecurityFacts):
            raise TypeError("AppSec provider must return SecurityFacts")
    except Exception:  # noqa: BLE001 — граница поставщика, сырые исключения не должны утекать.
        # Не логируем исключение/traceback: в них могут быть ответ AppSec и токен.
        # BaseException (остановка процесса и отмена) сюда не попадает.
        logger.warning("Не удалось получить корректные данные AppSec.")
        facts = build_facts(None, source_error="appsec_provider_failed")
    return evaluate(facts)


def _validate_payload(payload: object) -> None:
    # Проверяется только верхний уровень: схема findings пока не подтверждена.
    if payload is not None and not isinstance(payload, dict | list):
        raise TypeError("AppSec payload must be an object, an array or None")


def _build_assessment(payload: dict[str, Any] | list[Any] | None) -> _SecurityAssessment | None:
    """Возвращает оценку только при полном контракте всех трёх движков.

    Неизвестные либо отсутствующие severity/status намеренно не получают
    произвольный вес. Так ограниченная выборка или изменение контракта
    SourceCraft не превращаются в красивое, но недостоверное число.
    """

    if not isinstance(payload, dict) or set(payload) != {"engines"}:
        return None
    engines = payload["engines"]
    if not isinstance(engines, list) or len(engines) != len(APPSEC_ENGINES):
        return None

    by_engine: dict[str, tuple[dict[str, object], ...]] = {}
    for engine_payload in engines:
        parsed = _parse_complete_engine(engine_payload)
        if parsed is None:
            return None
        engine, groups = parsed
        if engine in by_engine:
            return None
        by_engine[engine] = groups

    if set(by_engine) != set(APPSEC_ENGINES):
        return None

    active_by_severity: defaultdict[str, int] = defaultdict(int)
    confirmed_open_critical = 0
    for groups in by_engine.values():
        for group in groups:
            severity = group["severity"]
            status = group["status"]
            count = group["count"]
            assert isinstance(severity, str)
            assert isinstance(status, str)
            assert isinstance(count, int)
            if status in SECURITY_ACTIVE_STATUSES:
                active_by_severity[severity] += count
            if severity == "CRITICAL" and status in SECURITY_CONFIRMED_OPEN_STATUSES:
                confirmed_open_critical += count

    frozen_counts = tuple(
        (severity, active_by_severity.get(severity, 0))
        for severity in sorted(APPSEC_SEVERITIES)
    )
    score = _security_score(dict(frozen_counts))
    return _SecurityAssessment(
        score=score,
        active_by_severity=frozen_counts,
        confirmed_open_critical=confirmed_open_critical,
    )


def _parse_complete_engine(
    payload: object,
) -> tuple[str, tuple[dict[str, object], ...]] | None:
    expected_fields = {
        "engine",
        "availability",
        "finding_count",
        "severities",
        "reason",
        "finding_groups",
        "completeness",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        return None

    engine = payload.get("engine")
    finding_count = payload.get("finding_count")
    severities = payload.get("severities")
    groups = payload.get("finding_groups")
    if (
        engine not in APPSEC_ENGINES
        or payload.get("availability") != "available"
        or payload.get("reason") is not None
        or payload.get("completeness") != "complete"
        or not isinstance(finding_count, int)
        or isinstance(finding_count, bool)
        or finding_count < 0
        or not isinstance(severities, list)
        or not isinstance(groups, list)
    ):
        return None

    parsed_groups = tuple(_parse_finding_group(group) for group in groups)
    if any(group is None for group in parsed_groups):
        return None
    safe_groups = tuple(group for group in parsed_groups if group is not None)
    if sum(group["count"] for group in safe_groups) != finding_count:
        return None
    grouped_severities = sorted({group["severity"] for group in safe_groups})
    if severities != grouped_severities:
        return None
    return engine, safe_groups


def _parse_finding_group(payload: object) -> dict[str, object] | None:
    expected_fields = {"severity", "status", "count"}
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        return None
    severity = payload.get("severity")
    status = payload.get("status")
    count = payload.get("count")
    if (
        severity not in APPSEC_SEVERITIES
        or status not in APPSEC_STATUSES
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count <= 0
    ):
        return None
    return {"severity": severity, "status": status, "count": count}


def _security_score(active_by_severity: dict[str, int]) -> float:
    penalty = sum(
        multiplier * min(active_by_severity.get(severity, 0), maximum_count)
        for severity, multiplier, maximum_count in SECURITY_SEVERITY_RULES
    )
    return float(max(0, 100 - penalty))


def _score_summary(assessment: _SecurityAssessment) -> str:
    if not assessment.active_finding_count:
        return "Полные результаты SAST, SCA и secret scanning не содержат открытых findings."
    return (
        "Security Score рассчитан по полным агрегированным результатам AppSec: "
        f"учтено открытых findings — {assessment.active_finding_count}."
    )


def _score_metrics(assessment: _SecurityAssessment) -> tuple[MetricResult, ...]:
    summary = "Получены полные обезличенные результаты SAST, SCA и secret scanning."
    evidence = Evidence(
        source=EVIDENCE_SOURCE,
        reference="appsec-defects",
        summary=summary,
    )
    return (
        MetricResult(
            code="appsec_data_coverage",
            value="complete",
            normalized_score=None,
            summary=summary,
            evidence=(evidence,),
        ),
        MetricResult(
            code="appsec_open_findings",
            value=assessment.active_finding_count,
            normalized_score=assessment.score,
            summary="Открытые findings учитываются по severity и статусу SourceCraft.",
            evidence=(evidence,),
        ),
        MetricResult(
            code=SECURITY_OPEN_CRITICAL_METRIC,
            value=assessment.confirmed_open_critical,
            normalized_score=None,
            summary="Критичные finding'и с подтверждённым открытым статусом ограничивают итоговый Score.",
            evidence=(evidence,),
        ),
    )


def _recommendations(assessment: _SecurityAssessment) -> tuple[Recommendation, ...]:
    recommendations: list[Recommendation] = []
    for severity, priority in (
        ("CRITICAL", RecommendationPriority.P0),
        ("HIGH", RecommendationPriority.P1),
        ("MEDIUM", RecommendationPriority.P2),
        ("LOW", RecommendationPriority.P3),
    ):
        count = assessment.count_for(severity)
        if not count:
            continue
        problem = f"AppSec сообщает об открытых findings уровня {severity}: {count}."
        action = "Проверьте finding'и в SourceCraft, исправьте подтверждённые проблемы и запустите скан повторно."
        if severity == "CRITICAL" and assessment.confirmed_open_critical:
            action = (
                "Сначала исправьте подтверждённые критичные finding'и и запустите скан повторно: "
                "они ограничивают общий Repo Health Score."
            )
        recommendations.append(
            Recommendation(
                code=f"appsec-open-{severity.lower()}",
                priority=priority,
                problem=problem,
                action=action,
                rationale="Основано на полном обезличенном результате AppSec SourceCraft.",
                expected_effect="После исправления и нового полного скана штраф Security Score уменьшится.",
                evidence=(
                    Evidence(
                        source=EVIDENCE_SOURCE,
                        reference="appsec-defects",
                        summary=f"Открытые findings уровня {severity}: {count}.",
                    ),
                ),
            )
        )
    return tuple(recommendations)


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
