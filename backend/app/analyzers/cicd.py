"""Оценка надёжности CI/CD по завершённым автоматическим запускам.

Сбор SourceCraft-данных намеренно остаётся вне модуля. Анализатор получает
проверенные факты, поэтому его формула воспроизводима в тестах и при повторном
анализе одного снимка.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

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

CATEGORY_CODE = "cicd"
EVIDENCE_SOURCE = "sourcecraft-cicd"
MAX_LINKED_FAILED_RUNS = 3
_SAFE_URL_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

MINIMUM_AUTOMATED_RUNS = 5
AUTOMATED_EVENT_TYPES = frozenset({"push", "pr_update", "schedule"})
SUCCESS_STATUS = "success"
FAILURE_STATUSES = frozenset({"failed", "timeout"})
KNOWN_STATUSES = frozenset(
    {
        "created",
        "prepared",
        "processing",
        "success",
        "failed",
        "canceled",
        "timeout",
        "skipped",
        "awaiting_approval",
        "rejected",
    }
)
# Допустимый тип запуска не обязательно участвует в Score (AUTOMATED_EVENT_TYPES).
KNOWN_EVENT_TYPES = frozenset(
    {"push", "pr_update", "manual", "restart", "schedule", "repository_event"}
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CiRunFact:
    """Минимальный безопасный факт об одном запуске CI.

    `slug`, а не API `id`, служит идентификатором: в наблюдённом SourceCraft
    ответе поле `id` было пустым, а `slug` — стабильным номером запуска.
    """

    slug: str
    status: str
    event_type: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.slug, str) or not self.slug.strip():
            raise ValueError("CI run slug must not be blank")
        if not isinstance(self.status, str) or self.status not in KNOWN_STATUSES:
            raise ValueError("CI run status is not supported")
        if not isinstance(self.event_type, str) or self.event_type not in KNOWN_EVENT_TYPES:
            raise ValueError("CI run event type is not supported")
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise ValueError("CI run created_at must include a timezone")

    @property
    def is_automated(self) -> bool:
        return self.event_type in AUTOMATED_EVENT_TYPES

    @property
    def has_outcome(self) -> bool:
        return self.status == SUCCESS_STATUS or self.status in FAILURE_STATUSES

    @property
    def is_successful(self) -> bool:
        return self.status == SUCCESS_STATUS


@dataclass(frozen=True, slots=True)
class CicdFacts:
    """Результат получения CI-запусков и признак полноты выборки."""

    # Сырым текстом ошибки могут быть ответ платформы или технические детали.
    runs: tuple[CiRunFact, ...] | None = field(default=None, repr=False)
    source_error: str | None = field(default=None, repr=False)
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")
        if self.source_error is not None:
            if not isinstance(self.source_error, str):
                raise TypeError("source_error must be a string or None")
            if not self.source_error.strip():
                raise ValueError("source_error must not be blank")
            if self.runs is not None or self.truncated:
                raise ValueError("source_error cannot be combined with CI runs")
        if self.truncated and self.runs is None:
            raise ValueError("truncated CI data requires collected runs")
        if self.runs is not None:
            if not isinstance(self.runs, tuple) or not all(
                isinstance(run, CiRunFact) for run in self.runs
            ):
                raise TypeError("CI runs must be a tuple of CiRunFact")
            _validate_unique_slugs(self.runs)


CicdFactsProvider = Callable[[RepositoryRef], CicdFacts]


def build_facts(
    runs: Iterable[CiRunFact] | None,
    *,
    source_error: str | None = None,
    truncated: bool = False,
) -> CicdFacts:
    """Собирает неизменяемый снимок CI без смешивания ошибки и результата."""

    return CicdFacts(
        runs=None if runs is None else tuple(runs),
        source_error=source_error,
        truncated=truncated,
    )


def evaluate(facts: CicdFacts, context: AnalysisContext) -> CategoryResult:
    """Оценивает долю успешных автоматических запусков за период контекста."""

    if facts.source_error is not None or facts.runs is None:
        return _unmeasured_result(
            DataStatus.UNAVAILABLE,
            "Не удалось получить историю запусков CI/CD.",
            "cicd_runs_unavailable",
            "unavailable",
            context.repository,
        )

    if facts.truncated:
        return _unmeasured_result(
            DataStatus.INSUFFICIENT_SAMPLE,
            "История запусков CI/CD прочитана не полностью.",
            "cicd_runs_truncated",
            "partial",
            context.repository,
        )

    period_runs = tuple(
        run for run in facts.runs if context.period_start <= run.created_at <= context.period_end
    )
    automated_runs = tuple(run for run in period_runs if run.is_automated)
    outcome_runs = tuple(run for run in automated_runs if run.has_outcome)

    if not facts.runs:
        return _unmeasured_result(
            DataStatus.INSUFFICIENT_SAMPLE,
            "История CI/CD доступна, но запусков в ней нет.",
            "cicd_no_runs",
            "empty",
            context.repository,
        )
    if not automated_runs:
        return _unmeasured_result(
            DataStatus.INSUFFICIENT_SAMPLE,
            "За период нет автоматических запусков CI/CD.",
            "cicd_no_automated_runs_in_period",
            "insufficient",
            context.repository,
        )
    if len(outcome_runs) < MINIMUM_AUTOMATED_RUNS:
        return _unmeasured_result(
            DataStatus.INSUFFICIENT_SAMPLE,
            (
                "Для оценки надёжности CI/CD нужно не менее "
                f"{MINIMUM_AUTOMATED_RUNS} автоматических запусков с итогом; "
                f"доступно {len(outcome_runs)}."
            ),
            "cicd_too_few_outcome_runs",
            "insufficient",
            context.repository,
        )

    successful_count = sum(run.is_successful for run in outcome_runs)
    failed_count = len(outcome_runs) - successful_count
    success_rate = successful_count / len(outcome_runs) * 100
    history_evidence = _evidence(
        "ci-runs",
        "История запусков CI/CD в SourceCraft. Страница показывает текущее состояние, "
        "а отчёт рассчитан за указанный период.",
        url=_runs_url(context.repository),
    )
    failed_evidence = _failed_run_evidence(outcome_runs, context.repository)
    metrics = (
        MetricResult(
            code="automated_ci_outcome_runs",
            value=len(outcome_runs),
            normalized_score=None,
            summary=(f"Автоматических запусков CI/CD с итогом за период: {len(outcome_runs)}."),
            evidence=(history_evidence,),
        ),
        MetricResult(
            code="automated_ci_success_rate",
            value=success_rate,
            normalized_score=success_rate,
            summary=(
                f"Успешно {successful_count} из {len(outcome_runs)} автоматических запусков CI/CD."
            ),
            evidence=(history_evidence, *failed_evidence),
        ),
    )
    return CategoryResult(
        category=CATEGORY_CODE,
        status=DataStatus.MEASURED,
        score=success_rate,
        summary=(f"Успешность автоматических запусков CI/CD за период — {success_rate:.0f} %."),
        metrics=metrics,
        recommendations=_recommendations(success_rate, failed_count, failed_evidence),
    )


def make_analyzer(facts_provider: CicdFactsProvider) -> Callable[[AnalysisContext], CategoryResult]:
    """Адаптирует поставщик CI-данных к общему runner анализа."""

    def analyze(context: AnalysisContext) -> CategoryResult:
        try:
            facts = facts_provider(context.repository)
            if not isinstance(facts, CicdFacts):
                raise TypeError("CI/CD provider must return CicdFacts")
        except Exception:  # noqa: BLE001 — не передаём исходный текст ошибки в общий журнал.
            logger.warning("Не удалось получить корректные данные CI/CD.")
            facts = build_facts(None, source_error="cicd_provider_failed")
        return evaluate(facts, context)

    return analyze


def _unmeasured_result(
    status: DataStatus,
    summary: str,
    reason: str,
    availability: str,
    repository: RepositoryRef,
) -> CategoryResult:
    runs_url = _runs_url(repository)
    return CategoryResult(
        category=CATEGORY_CODE,
        status=status,
        score=None,
        summary=summary,
        reason=reason,
        metrics=(
            MetricResult(
                code="cicd_data_availability",
                value=availability,
                normalized_score=None,
                summary=summary,
                evidence=(
                    _evidence(
                        "ci-runs",
                        (
                            f"{summary} Ссылка открывает текущую историю SourceCraft; "
                            "она может отличаться от периода отчёта."
                            if runs_url is not None
                            else summary
                        ),
                        url=runs_url,
                    ),
                ),
            ),
        ),
        recommendations=(),
    )


def _recommendations(
    success_rate: float,
    failed_count: int,
    failed_evidence: tuple[Evidence, ...],
) -> tuple[Recommendation, ...]:
    if not failed_count:
        return ()
    priority = RecommendationPriority.P1 if success_rate < 80 else RecommendationPriority.P2
    return (
        Recommendation(
            code="cicd-investigate-failed-runs",
            priority=priority,
            problem=f"Неуспешных автоматических запусков CI/CD за период: {failed_count}.",
            action="Разберите повторяющиеся падения CI/CD и устраните их причины.",
            rationale=(
                "Нестабильная автоматическая проверка замедляет поставку изменений "
                "и снижает доверие к результатам сборки."
            ),
            evidence=failed_evidence,
        ),
    )


def _failed_run_evidence(
    outcome_runs: tuple[CiRunFact, ...],
    repository: RepositoryRef,
) -> tuple[Evidence, ...]:
    """Даёт ссылки только на последние неуспешные запуски из оцениваемой выборки."""

    failed_runs = sorted(
        (run for run in outcome_runs if not run.is_successful),
        key=lambda run: (run.created_at, run.slug),
        reverse=True,
    )
    evidence: list[Evidence] = []
    for run in failed_runs:
        url = _runs_url(repository, run.slug)
        if url is None:
            continue
        evidence.append(
            _evidence(
                f"ci-run-{run.slug}",
                "Неуспешный запуск CI/CD: ошибка или тайм-аут.",
                url=url,
            )
        )
        if len(evidence) == MAX_LINKED_FAILED_RUNS:
            break
    return tuple(evidence)


def _runs_url(repository: RepositoryRef, run_slug: str | None = None) -> str | None:
    segments = (repository.organization_slug, repository.repository_slug)
    if run_slug is not None:
        segments += (run_slug,)
    if not all(isinstance(segment, str) and _SAFE_URL_SEGMENT.fullmatch(segment) for segment in segments):
        return None
    path = f"https://sourcecraft.dev/{segments[0]}/{segments[1]}/cicd/runs"
    return f"{path}/{run_slug}" if run_slug is not None else path


def _evidence(reference: str, summary: str, *, url: str | None = None) -> Evidence:
    return Evidence(source=EVIDENCE_SOURCE, reference=reference, summary=summary, url=url)


def _validate_unique_slugs(runs: tuple[CiRunFact, ...]) -> None:
    slugs = tuple(run.slug for run in runs)
    if len(set(slugs)) != len(slugs):
        raise ValueError("CI runs must not contain duplicate slugs")
