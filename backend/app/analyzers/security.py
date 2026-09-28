"""Нормализация данных AppSec из SourceCraft для категории безопасности."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote
from uuid import UUID

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
_SAFE_URL_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENGINE_PAGES = {"SAST": "sast", "SCA": "sca", "SECRETS": "secrets"}
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _SecurityAssessment:
    """Проверенная, но обезличенная основа для Security Score v1."""

    score: float
    active_by_severity: tuple[tuple[str, int], ...]
    active_by_engine_severity: tuple[tuple[str, str, int], ...]
    confirmed_open_critical: int
    confirmed_critical_engines: tuple[str, ...]

    @property
    def active_finding_count(self) -> int:
        return sum(count for _, count in self.active_by_severity)

    def count_for(self, severity: str) -> int:
        return dict(self.active_by_severity).get(severity, 0)

    def engines_for(self, severity: str | None = None) -> tuple[str, ...]:
        active = {
            engine
            for engine, group_severity, count in self.active_by_engine_severity
            if count and (severity is None or severity == group_severity)
        }
        return tuple(engine for engine in APPSEC_ENGINES if engine in active)


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
    scan_uuid: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _validate_payload(self.payload)
        if self.scan_uuid is not None:
            if not isinstance(self.scan_uuid, str):
                raise TypeError("AppSec scan UUID must be a string or None")
            try:
                parsed_uuid = UUID(self.scan_uuid)
            except ValueError as error:
                raise ValueError("AppSec scan UUID is invalid") from error
            if str(parsed_uuid) != self.scan_uuid or parsed_uuid.int == 0:
                raise ValueError("AppSec scan UUID is invalid")
            if self.payload is None:
                raise ValueError("AppSec scan UUID requires a payload")
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
    scan_uuid: str | None = None,
) -> SecurityFacts:
    """Создаёт факты, не смешивая сетевую ошибку с отсутствием данных."""

    return SecurityFacts(payload=payload, source_error=source_error, scan_uuid=scan_uuid)


def evaluate(facts: SecurityFacts, *, repository: RepositoryRef | None = None) -> CategoryResult:
    """Считает Security Score только по полному безопасному агрегату AppSec."""

    if facts.source_error is not None:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.ERROR,
            score=None,
            summary="Не удалось получить данные AppSec.",
            reason="appsec_source_error",
            metrics=(
                _availability_metric(
                    "error", "Запрос результатов AppSec завершился ошибкой.", repository
                ),
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
                    repository,
                ),
            ),
            recommendations=(),
        )

    assessment = _build_assessment(facts.payload)
    if assessment is None:
        verified_engines, partial_metrics = _partial_engine_metrics(
            facts.payload, repository, facts.scan_uuid
        )
        summary = (
            f"Подтверждён полный результат {', '.join(verified_engines)}, но данных "
            "для общего Security Score недостаточно."
            if verified_engines
            else (
                "Результат AppSec получен, но полнота сканов или безопасная схема "
                "severity/status пока не подтверждены."
            )
        )
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.INSUFFICIENT_SAMPLE,
            score=None,
            summary=summary,
            reason="appsec_coverage_not_confirmed",
            metrics=(
                _availability_metric(
                    "received",
                    "SourceCraft предоставил результат AppSec, но он не готов для Score.",
                    repository,
                    facts.scan_uuid,
                ),
                *partial_metrics,
            ),
            recommendations=(),
        )

    return CategoryResult(
        category=CATEGORY_CODE,
        status=DataStatus.MEASURED,
        score=assessment.score,
        summary=_score_summary(assessment),
        reason="security_score_v1",
        metrics=_score_metrics(assessment, repository, facts.scan_uuid),
        recommendations=_recommendations(assessment, repository, facts.scan_uuid),
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
        return _evaluate_provider(lambda: facts_provider(context.repository), context.repository)

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
        return _evaluate_provider(lambda: facts_provider(context), context.repository)

    return analyze


def _evaluate_provider(
    load_facts: Callable[[], SecurityFacts], repository: RepositoryRef
) -> CategoryResult:
    try:
        facts = load_facts()
        if not isinstance(facts, SecurityFacts):
            raise TypeError("AppSec provider must return SecurityFacts")
    except Exception:  # noqa: BLE001 — граница поставщика, сырые исключения не должны утекать.
        # Не логируем исключение/traceback: в них могут быть ответ AppSec и токен.
        # BaseException (остановка процесса и отмена) сюда не попадает.
        logger.warning("Не удалось получить корректные данные AppSec.")
        facts = build_facts(None, source_error="appsec_provider_failed")
    return evaluate(facts, repository=repository)


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
    active_by_engine_severity: defaultdict[tuple[str, str], int] = defaultdict(int)
    confirmed_open_critical = 0
    confirmed_critical_engines: set[str] = set()
    for engine, groups in by_engine.items():
        for group in groups:
            severity = group["severity"]
            status = group["status"]
            count = group["count"]
            assert isinstance(severity, str)
            assert isinstance(status, str)
            assert isinstance(count, int)
            if status in SECURITY_ACTIVE_STATUSES:
                active_by_severity[severity] += count
                active_by_engine_severity[(engine, severity)] += count
            if severity == "CRITICAL" and status in SECURITY_CONFIRMED_OPEN_STATUSES:
                confirmed_open_critical += count
                confirmed_critical_engines.add(engine)

    frozen_counts = tuple(
        (severity, active_by_severity.get(severity, 0)) for severity in sorted(APPSEC_SEVERITIES)
    )
    score = _security_score(dict(frozen_counts))
    return _SecurityAssessment(
        score=score,
        active_by_severity=frozen_counts,
        active_by_engine_severity=tuple(
            (engine, severity, count)
            for (engine, severity), count in sorted(active_by_engine_severity.items())
        ),
        confirmed_open_critical=confirmed_open_critical,
        confirmed_critical_engines=tuple(
            engine for engine in APPSEC_ENGINES if engine in confirmed_critical_engines
        ),
    )


def _partial_engine_metrics(
    payload: dict[str, Any] | list[Any] | None,
    repository: RepositoryRef | None,
    scan_uuid: str | None,
) -> tuple[tuple[str, ...], tuple[MetricResult, ...]]:
    """Показывает полные данные отдельных движков без оценки всей категории."""

    if not isinstance(payload, dict) or set(payload) != {"engines"}:
        return (), ()
    engines = payload["engines"]
    if not isinstance(engines, list) or len(engines) != len(APPSEC_ENGINES):
        return (), ()

    complete: dict[str, tuple[dict[str, object], ...]] = {}
    seen: set[str] = set()
    for raw_engine in engines:
        if not isinstance(raw_engine, dict):
            return (), ()
        engine = raw_engine.get("engine")
        if not isinstance(engine, str) or engine not in APPSEC_ENGINES or engine in seen:
            return (), ()
        seen.add(engine)
        parsed = _parse_complete_engine(raw_engine)
        if parsed is not None:
            complete[engine] = parsed[1]
    if not complete or len(complete) == len(APPSEC_ENGINES):
        return (), ()

    metrics: list[MetricResult] = []
    for engine in APPSEC_ENGINES:
        groups = complete.get(engine)
        if groups is None:
            continue
        by_severity: defaultdict[str, int] = defaultdict(int)
        for group in groups:
            if group["status"] in SECURITY_ACTIVE_STATUSES:
                severity, count = group["severity"], group["count"]
                assert isinstance(severity, str)
                assert isinstance(count, int)
                by_severity[severity] += count
        total = sum(by_severity.values())
        metrics.append(
            MetricResult(
                code=f"appsec_{engine.lower()}_open_findings",
                value=total,
                normalized_score=None,
                summary=f"Полный результат {engine}: {total} открытых групп.",
                evidence=(_engine_evidence(engine, repository, scan_uuid=scan_uuid),),
            )
        )
        for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            count = by_severity.get(severity, 0)
            if not count:
                continue
            metrics.append(
                MetricResult(
                    code=f"appsec_{engine.lower()}_open_{severity.lower()}",
                    value=count,
                    normalized_score=None,
                    summary=f"Открытые группы {engine} уровня {severity}: {count}.",
                    evidence=(
                        _engine_evidence(engine, repository, severity=severity, scan_uuid=scan_uuid),
                    ),
                )
            )
    verified_engines = tuple(engine for engine in APPSEC_ENGINES if engine in complete)
    return verified_engines, tuple(metrics)


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


def _score_metrics(
    assessment: _SecurityAssessment, repository: RepositoryRef | None, scan_uuid: str | None
) -> tuple[MetricResult, ...]:
    summary = "Получены полные обезличенные результаты SAST, SCA и secret scanning."
    overview_url = _security_url(repository, scan_uuid=scan_uuid)
    evidence = Evidence(
        source=EVIDENCE_SOURCE,
        reference="appsec-defects",
        summary=(
            f"{summary} {_link_context(scan_uuid, 'обзор')}"
            if overview_url is not None
            else summary
        ),
        url=overview_url,
    )
    finding_evidence = tuple(
        _engine_evidence(engine, repository, scan_uuid=scan_uuid)
        for engine in assessment.engines_for()
    )
    critical_evidence = tuple(
        _engine_evidence(engine, repository, scan_uuid=scan_uuid)
        for engine in assessment.confirmed_critical_engines
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
            evidence=(evidence, *finding_evidence),
        ),
        MetricResult(
            code=SECURITY_OPEN_CRITICAL_METRIC,
            value=assessment.confirmed_open_critical,
            normalized_score=None,
            summary="Критичные finding'и с подтверждённым открытым статусом ограничивают итоговый Score.",
            evidence=(evidence, *critical_evidence),
        ),
    )


def _recommendations(
    assessment: _SecurityAssessment, repository: RepositoryRef | None, scan_uuid: str | None
) -> tuple[Recommendation, ...]:
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
                evidence=tuple(
                    _engine_evidence(engine, repository, severity=severity, scan_uuid=scan_uuid)
                    for engine in assessment.engines_for(severity)
                ),
            )
        )
    return tuple(recommendations)


def _availability_metric(
    value: str, summary: str, repository: RepositoryRef | None = None,
    scan_uuid: str | None = None,
) -> MetricResult:
    """Создаёт метрику доступности без содержимого findings и потенциальных секретов."""

    overview_url = _security_url(repository, scan_uuid=scan_uuid)
    return MetricResult(
        code="appsec_data_availability",
        value=value,
        normalized_score=None,
        summary=summary,
        evidence=(
            Evidence(
                source=EVIDENCE_SOURCE,
                reference="appsec-defects",
                summary=(
                    f"{summary} {_link_context(scan_uuid, 'обзор')}"
                    if overview_url is not None
                    else summary
                ),
                url=overview_url,
            ),
        ),
    )


def _engine_evidence(
    engine: str, repository: RepositoryRef | None, *,
    severity: str | None = None, scan_uuid: str | None = None,
) -> Evidence:
    summary = f"Открытые findings {engine} учтены в отчёте."
    if severity is not None:
        summary = f"Открытые findings {engine} уровня {severity} учтены в отчёте."
    url = _security_url(repository, engine, scan_uuid=scan_uuid)
    if url is not None:
        summary += f" {_link_context(scan_uuid, 'список')}"
    return Evidence(
        source=EVIDENCE_SOURCE,
        reference=f"appsec-{engine.lower()}-defects",
        summary=summary,
        url=url,
    )


def _link_context(scan_uuid: str | None, page: str) -> str:
    if scan_uuid is not None:
        return (
            f"Ссылка открывает {page} результатов того же скана в SourceCraft. "
            "Статусы находок могли измениться позже."
        )
    return f"Ссылка открывает текущий {page} SourceCraft, который может отличаться от снимка отчёта."


def _security_url(
    repository: RepositoryRef | None, engine: str | None = None, *, scan_uuid: str | None = None
) -> str | None:
    if repository is None:
        return None
    segments = (repository.organization_slug, repository.repository_slug)
    if not all(isinstance(item, str) and _SAFE_URL_SEGMENT.fullmatch(item) for item in segments):
        return None
    base = f"https://sourcecraft.dev/{segments[0]}/{segments[1]}/security"
    if engine is None:
        return f"{base}/overview?scanId={scan_uuid}" if scan_uuid else f"{base}/overview"
    page = _ENGINE_PAGES.get(engine)
    if page is None:
        return None
    url = f"{base}/{page}"
    if engine != "SAST" and scan_uuid is None:
        return url
    # Без явного фильтра страница SourceCraft может открыться с сохранённым
    # ограничением по сканеру и показать лишь часть учтённых SAST-групп.
    operands: list[dict[str, object]] = []
    if engine == "SAST":
        predicates = [
            {"predicate": {"field": "status", "operator": "OPERATOR_EQ", "stringValue": status}}
            for status in sorted(SECURITY_ACTIVE_STATUSES)
        ]
        operands.append({"or": {"operands": predicates}})
    if scan_uuid is not None:
        operands.append(
            {"predicate": {"field": "scan_id", "operator": "OPERATOR_EQ", "stringValue": scan_uuid}}
        )
    filter_json = json.dumps(
        {"and": {"operands": operands}},
        separators=(",", ":"),
    )
    # SourceCraft URL кодирует JSON дважды; формат получен через фильтр в UI.
    return f"{url}?filter={quote(quote(filter_json, safe=''), safe='')}"
