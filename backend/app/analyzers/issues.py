"""Категория Issues: насколько проект отвечает на обращения и разбирает задачи.

Категория оценивает работу с обращениями, а не их количество: у популярного проекта
открытых задач всегда больше, и само число ни о чём не говорит. Значение имеет доля
задач, брошенных без движения, динамика разбора и время до закрытия.

Модуль разделён на две части. `collect` обращается к SourceCraft и возвращает факты.
`evaluate` — чистая функция: она не ходит в сеть и не смотрит на системное время,
поэтому на одних и тех же фактах всегда даёт один и тот же результат.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median
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
from backend.app.integrations.sourcecraft import SourceCraftClient, SourceCraftClientError

CATEGORY_CODE = "issues"

EVIDENCE_SOURCE = "sourcecraft-issues"

# Задача считается брошенной, если её не двигали дольше этого срока. Порог намеренно
# мягче 30 дней из примера ТЗ: для небольших открытых проектов ответ раз в месяц —
# нормальный режим, а не признак болезни.
STALE_AFTER_DAYS = 90

# Порог для предупреждения о задачах, которые скоро станут брошенными.
AGING_AFTER_DAYS = 30

# Веса внутри категории. Нормируются по доступным метрикам, как и веса категорий в ядре.
METRIC_WEIGHTS = {
    "stale_open_ratio": 45.0,
    "backlog_trend": 30.0,
    "median_days_to_close": 25.0,
}

# Статусы SourceCraft: отменённая задача не является решённой, иначе оценку можно
# поднять, закрыв всё как «отменено».
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 5


@dataclass(frozen=True, slots=True)
class IssueFact:
    """Минимум сведений об одной задаче, нужный для расчёта."""

    slug: str
    title: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    status_type: str

    @property
    def is_resolved(self) -> bool:
        return self.status_type == STATUS_COMPLETED

    @property
    def is_cancelled(self) -> bool:
        return self.status_type == STATUS_CANCELLED


@dataclass(frozen=True, slots=True)
class IssuesFacts:
    """Собранные факты вместе с оценкой их полноты.

    Признаки `*_truncated` обязательны: без них нельзя отличить «в проекте мало задач»
    от «мы прочитали только первые страницы списка».
    """

    open_issues: tuple[IssueFact, ...] = ()
    closed_issues: tuple[IssueFact, ...] = ()
    open_truncated: bool = False
    closed_truncated: bool = False
    error: str | None = None

    @property
    def total_count(self) -> int:
        return len(self.open_issues) + len(self.closed_issues)

    @property
    def truncated(self) -> bool:
        return self.open_truncated or self.closed_truncated


def parse_datetime(value: Any) -> datetime | None:
    """Разбирает метку времени RFC3339; непригодное значение даёт None, а не исключение."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_issue(raw: Any) -> IssueFact | None:
    """Превращает ответ API в факт. Задача без обязательных дат пропускается."""
    if not isinstance(raw, dict):
        return None

    created_at = parse_datetime(raw.get("created_at"))
    updated_at = parse_datetime(raw.get("updated_at"))
    if created_at is None or updated_at is None:
        return None

    status = raw.get("status") or {}
    return IssueFact(
        slug=str(raw.get("slug") or raw.get("id") or ""),
        title=str(raw.get("title") or ""),
        created_at=created_at,
        updated_at=updated_at,
        completed_at=parse_datetime(raw.get("completed_at")),
        status_type=str(status.get("status_type") or ""),
    )


def build_facts(
    open_items: Iterable[Any],
    closed_items: Iterable[Any],
    *,
    open_truncated: bool = False,
    closed_truncated: bool = False,
) -> IssuesFacts:
    """Собирает факты из сырых ответов API или из сохранённых фикстур."""
    return IssuesFacts(
        open_issues=tuple(filter(None, (parse_issue(item) for item in open_items))),
        closed_issues=tuple(filter(None, (parse_issue(item) for item in closed_items))),
        open_truncated=open_truncated,
        closed_truncated=closed_truncated,
    )


def _fetch_pages(
    client: SourceCraftClient,
    path: str,
    *,
    status: str,
    max_pages: int,
) -> tuple[list[Any], bool]:
    """Обходит страницы списка задач и сообщает, была ли выборка оборвана.

    Пагинация живёт здесь временно: когда SourceCraftClient получит собственный
    метод обхода страниц, эта функция должна исчезнуть.
    """
    params: dict[str, str | int] = {"page_size": PAGE_SIZE, "filter": f"status={status}"}
    items: list[Any] = []

    for page in range(1, max_pages + 1):
        payload = client.get_json(path, params=params)
        if not isinstance(payload, dict):
            raise SourceCraftClientError("SourceCraft issues response must be an object")

        items.extend(payload.get("issues") or [])
        next_token = payload.get("next_page_token") or ""
        if not next_token:
            return items, False
        if page == max_pages:
            return items, True
        params["page_token"] = next_token

    return items, True


def collect(
    client: SourceCraftClient,
    repository: RepositoryRef,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> IssuesFacts:
    """Обращается к SourceCraft и возвращает факты. Оценок не выставляет."""
    path = f"/repos/{repository.organization_slug}/{repository.repository_slug}/issues"

    try:
        open_items, open_truncated = _fetch_pages(client, path, status="open", max_pages=max_pages)
        closed_items, closed_truncated = _fetch_pages(
            client, path, status="closed", max_pages=max_pages
        )
    except SourceCraftClientError as error:
        # Недоступность источника — тоже факт о репозитории, а не плохая оценка.
        return IssuesFacts(error=str(error))

    return build_facts(
        open_items,
        closed_items,
        open_truncated=open_truncated,
        closed_truncated=closed_truncated,
    )


def _linear_score(value: float, *, best: float, worst: float) -> float:
    """Переводит значение метрики в 0–100 линейно между «хорошо» и «плохо»."""
    if best == worst:
        raise ValueError("best and worst thresholds must differ")

    ratio = (value - worst) / (best - worst)
    return max(0.0, min(1.0, ratio)) * 100


def _in_period(moment: datetime, context: AnalysisContext) -> bool:
    return context.period_start <= moment <= context.period_end


def _issue_url(repository: RepositoryRef, slug: str) -> str | None:
    """Ссылка на задачу в интерфейсе SourceCraft; без web_url ссылка не придумывается."""
    if not repository.web_url or not slug:
        return None
    return f"{repository.web_url.rstrip('/')}/issues/{slug}"


def _stale_metric(
    facts: IssuesFacts, context: AnalysisContext
) -> tuple[MetricResult, float] | None:
    """Доля открытых задач, которых не двигали дольше порога."""
    if not facts.open_issues:
        return None

    stale = [
        issue
        for issue in facts.open_issues
        if (context.analyzed_at - issue.updated_at).days > STALE_AFTER_DAYS
    ]
    ratio = len(stale) / len(facts.open_issues)
    score = _linear_score(ratio, best=0.05, worst=0.5)

    evidence = tuple(
        Evidence(
            source=EVIDENCE_SOURCE,
            reference=issue.slug,
            summary=(
                f"«{issue.title}» без изменений с {issue.updated_at.date().isoformat()} "
                f"({(context.analyzed_at - issue.updated_at).days} дн.)"
            ),
            url=_issue_url(context.repository, issue.slug),
        )
        for issue in sorted(stale, key=lambda issue: issue.updated_at)[:5]
    )

    metric = MetricResult(
        code="stale_open_ratio",
        value=round(ratio, 4),
        normalized_score=score,
        summary=(
            f"{len(stale)} из {len(facts.open_issues)} открытых задач "
            f"не обновлялись дольше {STALE_AFTER_DAYS} дней"
        ),
        evidence=evidence,
    )
    return metric, score


def _backlog_metric(
    facts: IssuesFacts, context: AnalysisContext
) -> tuple[MetricResult, float] | None:
    """Соотношение решённых и созданных задач за период анализа."""
    created = [issue for issue in facts.open_issues + facts.closed_issues
               if _in_period(issue.created_at, context)]
    resolved = [
        issue
        for issue in facts.closed_issues
        if issue.is_resolved and issue.completed_at is not None
        and _in_period(issue.completed_at, context)
    ]

    if not created:
        # Новых задач за период не было: динамику разбора измерять нечем.
        return None

    ratio = len(resolved) / len(created)
    score = _linear_score(ratio, best=1.0, worst=0.25)

    metric = MetricResult(
        code="backlog_trend",
        value=round(ratio, 4),
        normalized_score=score,
        summary=(
            f"за период создано {len(created)} задач, решено {len(resolved)}"
        ),
    )
    return metric, score


def _resolution_time_metric(facts: IssuesFacts) -> tuple[MetricResult, float] | None:
    """Медианное время от создания до решения задачи."""
    durations = [
        (issue.completed_at - issue.created_at).days
        for issue in facts.closed_issues
        if issue.is_resolved and issue.completed_at is not None
    ]

    # Поле completed_at заполнено не всегда; на единичных наблюдениях медиана
    # недостоверна, поэтому метрика просто не участвует в оценке.
    if len(durations) < 3:
        return None

    value = float(median(durations))
    score = _linear_score(value, best=7.0, worst=180.0)

    metric = MetricResult(
        code="median_days_to_close",
        value=value,
        normalized_score=score,
        summary=f"медиана времени до решения — {value:.0f} дн. по {len(durations)} задачам",
    )
    return metric, score


def _build_recommendations(
    facts: IssuesFacts,
    context: AnalysisContext,
    metrics: Sequence[MetricResult],
) -> tuple[Recommendation, ...]:
    """Рекомендации строятся только по измеренным метрикам и ссылаются на факты."""
    by_code = {metric.code: metric for metric in metrics}
    recommendations: list[Recommendation] = []

    stale = by_code.get("stale_open_ratio")
    if stale is not None and isinstance(stale.value, float) and stale.value > 0.2:
        recommendations.append(
            Recommendation(
                code="issues-triage-stale",
                priority=RecommendationPriority.P1,
                problem=stale.summary,
                action=(
                    "Разберите перечисленные задачи: закройте неактуальные, "
                    "остальным назначьте ответственного и срок."
                ),
                rationale=(
                    "Задачи без движения показывают, что обращения пользователей "
                    "остаются без ответа, и снижают доверие к проекту."
                ),
                expected_effect="Сокращение доли брошенных задач повысит оценку категории Issues.",
                evidence=stale.evidence,
            )
        )

    backlog = by_code.get("backlog_trend")
    if backlog is not None and isinstance(backlog.value, float) and backlog.value < 0.5:
        recommendations.append(
            Recommendation(
                code="issues-backlog-growing",
                priority=RecommendationPriority.P2,
                problem=f"Очередь задач растёт: {backlog.summary}.",
                action="Планируйте разбор задач регулярно, а не по остаточному принципу.",
                rationale="Когда задачи создаются быстрее, чем решаются, очередь копится.",
                expected_effect="Выравнивание темпа разбора улучшит динамику категории Issues.",
            )
        )

    aging = [
        issue
        for issue in facts.open_issues
        if AGING_AFTER_DAYS < (context.analyzed_at - issue.updated_at).days <= STALE_AFTER_DAYS
    ]
    if aging and not recommendations:
        recommendations.append(
            Recommendation(
                code="issues-aging-watch",
                priority=RecommendationPriority.P3,
                problem=f"{len(aging)} задач не обновлялись дольше {AGING_AFTER_DAYS} дней.",
                action="Отметьте их статус, чтобы они не перешли в брошенные.",
                rationale="Ранний разбор дешевле, чем возврат к задаче через несколько месяцев.",
            )
        )

    return tuple(recommendations)


def evaluate(facts: IssuesFacts, context: AnalysisContext) -> CategoryResult:
    """Превращает факты в оценку категории. Без сети и без обращения к текущему времени."""
    if facts.error is not None:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.UNAVAILABLE,
            score=None,
            summary="Не удалось получить задачи репозитория.",
            reason=facts.error,
        )

    if facts.total_count == 0:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.NOT_APPLICABLE,
            score=None,
            summary="В репозитории нет задач: работу с обращениями оценивать не на чем.",
            reason="Трекер задач не используется.",
        )

    if facts.truncated:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.INSUFFICIENT_SAMPLE,
            score=None,
            summary=(
                f"Просмотрено {facts.total_count} задач, список не дочитан до конца."
            ),
            reason="Выборка неполная: доля брошенных задач по ней не определяется.",
        )

    measured: list[tuple[MetricResult, float]] = [
        result
        for result in (
            _stale_metric(facts, context),
            _backlog_metric(facts, context),
            _resolution_time_metric(facts),
        )
        if result is not None
    ]

    if not measured:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.INSUFFICIENT_SAMPLE,
            score=None,
            summary=f"Задачи есть ({facts.total_count}), но ни одна метрика не применима.",
            reason="Нет открытых задач за период и слишком мало решённых для медианы.",
        )

    metrics = tuple(metric for metric, _ in measured)
    total_weight = sum(METRIC_WEIGHTS[metric.code] for metric in metrics)
    score = sum(METRIC_WEIGHTS[metric.code] * value for metric, value in measured) / total_weight

    return CategoryResult(
        category=CATEGORY_CODE,
        status=DataStatus.MEASURED,
        score=score,
        summary="; ".join(metric.summary for metric in metrics),
        metrics=metrics,
        recommendations=_build_recommendations(facts, context, metrics),
    )
