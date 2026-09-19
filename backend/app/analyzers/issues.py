"""Категория Issues: насколько проект отвечает на обращения и разбирает задачи.

Категория оценивает работу с обращениями, а не их количество: у популярного проекта
открытых задач всегда больше, и само число ни о чём не говорит. Значение имеет доля
задач, брошенных без движения, динамика разбора и время до закрытия.

Модуль разделён на две части. `collect` обращается к SourceCraft и возвращает факты.
`evaluate` — чистая функция: она не ходит в сеть и не смотрит на системное время,
поэтому на одних и тех же фактах всегда даёт один и тот же результат.

Открытые и закрытые задачи запрашиваются независимо. Если один из списков не удалось
получить или он оборван по лимиту страниц, категория не обнуляется целиком: метрики,
которым не хватает данных, выпадают, а вес перераспределяется между остальными.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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

# Пороги нормализации. Вынесены в константы, чтобы тесты и документация ссылались
# на одно место, а не повторяли числа.
STALE_RATIO_BEST = 0.05
STALE_RATIO_WORST = 0.5
BACKLOG_RATIO_BEST = 1.0
BACKLOG_RATIO_WORST = 0.25
RESOLUTION_DAYS_BEST = 7.0
RESOLUTION_DAYS_WORST = 180.0

# Медиана по одному-двум наблюдениям недостоверна.
MIN_RESOLUTION_SAMPLE = 3

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

# Фильтр API: open / in_progress / closed. Задачи в работе — это ещё открытый
# трекер, а не решённые; без in_progress они выпадали из обеих выборок.
OPEN_STATUS_FILTERS = ("open", "in_progress")
CLOSED_STATUS_FILTERS = ("closed",)

PAGE_SIZE = 100

# Бюджет страниц рассчитан на самый крупный трекер платформы (около 2400 задач).
# Обрыв выборки остаётся возможным, но перестаёт быть обычным режимом работы.
DEFAULT_MAX_PAGES = 30

SECONDS_PER_DAY = 86400.0


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

    Признаки `*_truncated` и `*_error` обязательны: без них нельзя отличить
    «в проекте мало задач» от «список прочитан не полностью» и от «источник недоступен».
    """

    open_issues: tuple[IssueFact, ...] = ()
    closed_issues: tuple[IssueFact, ...] = ()
    open_truncated: bool = False
    closed_truncated: bool = False
    open_error: str | None = None
    closed_error: str | None = None
    skipped_count: int = 0

    @property
    def open_available(self) -> bool:
        return self.open_error is None

    @property
    def closed_available(self) -> bool:
        return self.closed_error is None

    @property
    def total_count(self) -> int:
        return len(self.open_issues) + len(self.closed_issues)

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(error for error in (self.open_error, self.closed_error) if error)


def parse_datetime(value: Any) -> datetime | None:
    """Разбирает метку времени RFC3339 и приводит её к UTC.

    Часовой пояс нормализуется намеренно: сравнение «наивной» и «осведомлённой» даты
    в Python выбрасывает TypeError, и одна битая запись уронила бы весь анализ.
    """
    if not isinstance(value, str) or not value:
        return None

    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"

    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None

    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def parse_issue(raw: Any) -> IssueFact | None:
    """Превращает ответ API в факт. Задача без обязательных дат пропускается."""
    if not isinstance(raw, dict):
        return None

    created_at = parse_datetime(raw.get("created_at"))
    updated_at = parse_datetime(raw.get("updated_at"))
    if created_at is None or updated_at is None:
        return None

    status = raw.get("status")
    status_type = status.get("status_type") if isinstance(status, dict) else None

    return IssueFact(
        slug=str(raw.get("slug") or raw.get("id") or ""),
        title=str(raw.get("title") or ""),
        created_at=created_at,
        updated_at=updated_at,
        completed_at=parse_datetime(raw.get("completed_at")),
        status_type=str(status_type or ""),
    )


def _parse_many(items: Iterable[Any]) -> tuple[tuple[IssueFact, ...], int]:
    parsed: list[IssueFact] = []
    skipped = 0
    for item in items:
        fact = parse_issue(item)
        if fact is None:
            skipped += 1
        else:
            parsed.append(fact)
    return tuple(parsed), skipped


def build_facts(
    open_items: Iterable[Any],
    closed_items: Iterable[Any],
    *,
    open_truncated: bool = False,
    closed_truncated: bool = False,
    open_error: str | None = None,
    closed_error: str | None = None,
) -> IssuesFacts:
    """Собирает факты из сырых ответов API или из сохранённых фикстур."""
    open_issues, open_skipped = _parse_many(open_items)
    closed_issues, closed_skipped = _parse_many(closed_items)

    return IssuesFacts(
        open_issues=open_issues,
        closed_issues=closed_issues,
        open_truncated=open_truncated,
        closed_truncated=closed_truncated,
        open_error=open_error,
        closed_error=closed_error,
        skipped_count=open_skipped + closed_skipped,
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
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

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
        params["page_token"] = str(next_token)

    return items, True


def _fetch_status(
    client: SourceCraftClient,
    path: str,
    *,
    status: str,
    max_pages: int,
) -> tuple[list[Any], bool, str | None]:
    """Забирает один список задач. Сбой источника возвращается как факт, не как исключение."""
    try:
        items, truncated = _fetch_pages(client, path, status=status, max_pages=max_pages)
    except SourceCraftClientError as error:
        return [], False, str(error)
    return items, truncated, None


def _dedupe_raw_issues(items: list[Any]) -> list[Any]:
    """Убирает пересечение open и in_progress по id, иначе по slug."""
    unique: list[Any] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            unique.append(item)
            continue
        key = str(item.get("id") or item.get("slug") or "")
        if key:
            if key in seen:
                continue
            seen.add(key)
        unique.append(item)
    return unique


def _fetch_status_group(
    client: SourceCraftClient,
    path: str,
    *,
    statuses: tuple[str, ...],
    max_pages: int,
) -> tuple[list[Any], bool, str | None]:
    """Собирает несколько статусов. Ошибка одного фильтра не затирает остальные."""
    combined: list[Any] = []
    truncated = False
    errors: list[str] = []
    successes = 0

    for status in statuses:
        items, is_truncated, error = _fetch_status(
            client, path, status=status, max_pages=max_pages
        )
        if error is not None:
            errors.append(error)
            continue
        successes += 1
        combined.extend(items)
        truncated = truncated or is_truncated

    if successes == 0:
        return [], False, "; ".join(errors)
    return _dedupe_raw_issues(combined), truncated, None


def collect(
    client: SourceCraftClient,
    repository: RepositoryRef,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> IssuesFacts:
    """Обращается к SourceCraft и возвращает факты. Оценок не выставляет."""
    org = urllib.parse.quote(repository.organization_slug, safe="")
    repo = urllib.parse.quote(repository.repository_slug, safe="")
    path = f"/repos/{org}/{repo}/issues"

    open_items, open_truncated, open_error = _fetch_status_group(
        client, path, statuses=OPEN_STATUS_FILTERS, max_pages=max_pages
    )
    closed_items, closed_truncated, closed_error = _fetch_status_group(
        client, path, statuses=CLOSED_STATUS_FILTERS, max_pages=max_pages
    )

    return build_facts(
        open_items,
        closed_items,
        open_truncated=open_truncated,
        closed_truncated=closed_truncated,
        open_error=open_error,
        closed_error=closed_error,
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
    return f"{repository.web_url.rstrip('/')}/issues/{urllib.parse.quote(slug, safe='')}"


def _age_in_days(context: AnalysisContext, moment: datetime) -> int:
    return (context.analyzed_at - moment).days


def _closed_at(issue: IssueFact) -> datetime | None:
    """Дата выхода задачи из открытого состояния.

    У части репозиториев (часто после миграции с GitHub) completed_at пуст.
    Тогда единственная доступная отметка — updated_at закрытой задачи.
    """
    if not (issue.is_resolved or issue.is_cancelled):
        return None
    return issue.completed_at or issue.updated_at


def _closed_in_period(issue: IssueFact, context: AnalysisContext) -> bool:
    closed = _closed_at(issue)
    return closed is not None and _in_period(closed, context)


def _stale_metric(
    facts: IssuesFacts, context: AnalysisContext
) -> tuple[MetricResult, float] | None:
    """Доля открытых задач, которых не двигали дольше порога.

    Требует полного списка открытых задач: по оборванной выборке доля не определяется.
    """
    if not facts.open_available or facts.open_truncated or not facts.open_issues:
        return None

    stale = [
        issue
        for issue in facts.open_issues
        if _age_in_days(context, issue.updated_at) > STALE_AFTER_DAYS
    ]
    ratio = len(stale) / len(facts.open_issues)
    score = _linear_score(ratio, best=STALE_RATIO_BEST, worst=STALE_RATIO_WORST)

    evidence = tuple(
        Evidence(
            source=EVIDENCE_SOURCE,
            reference=issue.slug,
            summary=(
                f"«{issue.title}» без изменений с {issue.updated_at.date().isoformat()} "
                f"({_age_in_days(context, issue.updated_at)} дн.)"
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
    """Соотношение решённых и созданных задач за период анализа.

    Нужны оба списка целиком: иначе «создано» и «решено» считаются по разным выборкам.
    """
    if not (facts.open_available and facts.closed_available):
        return None
    if facts.open_truncated or facts.closed_truncated:
        return None

    created = [
        issue
        for issue in facts.open_issues + facts.closed_issues
        if _in_period(issue.created_at, context)
    ]
    resolved = [
        issue
        for issue in facts.closed_issues
        if issue.is_resolved and _closed_in_period(issue, context)
    ]
    cancelled = [
        issue
        for issue in facts.closed_issues
        if issue.is_cancelled and _closed_in_period(issue, context)
    ]

    if not created:
        # Новых задач за период не было: динамику разбора измерять нечем.
        return None

    ratio = len(resolved) / len(created)
    score = _linear_score(min(ratio, 1.0), best=BACKLOG_RATIO_BEST, worst=BACKLOG_RATIO_WORST)

    summary = f"за период создано {len(created)} задач, решено {len(resolved)}"
    fallback = sum(1 for issue in resolved if issue.completed_at is None)
    if fallback:
        summary += f"; для {fallback} дата закрытия взята из updated_at"
    if cancelled:
        summary += f", отменено {len(cancelled)} (в решённые не входят)"

    metric = MetricResult(
        code="backlog_trend",
        value=round(ratio, 4),
        normalized_score=score,
        summary=summary,
    )
    return metric, score


def _resolution_time_metric(
    facts: IssuesFacts, context: AnalysisContext
) -> tuple[MetricResult, float] | None:
    """Медианное время от создания до решения задачи внутри периода анализа."""
    if not facts.closed_available or facts.closed_truncated:
        return None

    resolved = [
        issue
        for issue in facts.closed_issues
        if issue.is_resolved
        and _closed_in_period(issue, context)
        and (_closed_at(issue) or issue.created_at) >= issue.created_at
    ]

    # Поле completed_at заполняется не всеми проектами; на единичных наблюдениях
    # медиана недостоверна, поэтому метрика просто не участвует в оценке.
    if len(resolved) < MIN_RESOLUTION_SAMPLE:
        return None

    durations = {
        issue.slug: ((_closed_at(issue) or issue.created_at) - issue.created_at).total_seconds()
        / SECONDS_PER_DAY
        for issue in resolved
    }
    value = float(median(durations.values()))
    score = _linear_score(value, best=RESOLUTION_DAYS_BEST, worst=RESOLUTION_DAYS_WORST)

    slowest = sorted(resolved, key=lambda issue: durations[issue.slug], reverse=True)[:3]
    evidence = tuple(
        Evidence(
            source=EVIDENCE_SOURCE,
            reference=issue.slug,
            summary=f"«{issue.title}» решалась {durations[issue.slug]:.1f} дн.",
            url=_issue_url(context.repository, issue.slug),
        )
        for issue in slowest
    )

    metric = MetricResult(
        code="median_days_to_close",
        value=round(value, 2),
        normalized_score=score,
        summary=f"медиана времени до решения — {value:.0f} дн. по {len(resolved)} задачам",
        evidence=evidence,
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
        if AGING_AFTER_DAYS < _age_in_days(context, issue.updated_at) <= STALE_AFTER_DAYS
    ]
    if aging and not recommendations:
        recommendations.append(
            Recommendation(
                code="issues-aging-watch",
                priority=RecommendationPriority.P3,
                problem=f"{len(aging)} задач не обновлялись дольше {AGING_AFTER_DAYS} дней.",
                action="Отметьте их статус, чтобы они не перешли в брошенные.",
                rationale="Ранний разбор дешевле, чем возврат к задаче через несколько месяцев.",
                evidence=tuple(
                    Evidence(
                        source=EVIDENCE_SOURCE,
                        reference=issue.slug,
                        summary=(
                            f"«{issue.title}» без изменений "
                            f"{_age_in_days(context, issue.updated_at)} дн."
                        ),
                        url=_issue_url(context.repository, issue.slug),
                    )
                    for issue in sorted(aging, key=lambda issue: issue.updated_at)[:5]
                ),
            )
        )

    return tuple(recommendations)


def _unmeasured_result(facts: IssuesFacts, summary: str) -> CategoryResult:
    """Возвращает результат без оценки, различая недоступность и неполноту данных."""
    if facts.errors:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.UNAVAILABLE,
            score=None,
            summary=summary,
            reason="; ".join(facts.errors),
        )

    if facts.open_truncated or facts.closed_truncated:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.INSUFFICIENT_SAMPLE,
            score=None,
            summary=summary,
            reason="Списки задач прочитаны не полностью: доли и динамика не определяются.",
        )

    if facts.skipped_count:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.INSUFFICIENT_SAMPLE,
            score=None,
            summary=summary,
            reason=f"{facts.skipped_count} задач пропущены из-за неполных данных.",
        )

    return CategoryResult(
        category=CATEGORY_CODE,
        status=DataStatus.INSUFFICIENT_SAMPLE,
        score=None,
        summary=summary,
        reason="Нет открытых задач за период и слишком мало решённых для медианы.",
    )


def evaluate(facts: IssuesFacts, context: AnalysisContext) -> CategoryResult:
    """Превращает факты в оценку категории. Без сети и без обращения к текущему времени."""
    if facts.open_error and facts.closed_error:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.UNAVAILABLE,
            score=None,
            summary="Не удалось получить задачи репозитория.",
            reason="; ".join(facts.errors),
        )

    if facts.open_available and facts.closed_available and facts.total_count == 0:
        if facts.skipped_count:
            return CategoryResult(
                category=CATEGORY_CODE,
                status=DataStatus.INSUFFICIENT_SAMPLE,
                score=None,
                summary=(
                    f"Получено {facts.skipped_count} задач, но ни у одной нет обязательных дат."
                ),
                reason="Записи без created_at или updated_at нельзя измерить.",
            )
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.NOT_APPLICABLE,
            score=None,
            summary="В репозитории нет задач: работу с обращениями оценивать не на чем.",
            reason="Трекер задач не используется.",
        )

    measured: list[tuple[MetricResult, float]] = [
        result
        for result in (
            _stale_metric(facts, context),
            _backlog_metric(facts, context),
            _resolution_time_metric(facts, context),
        )
        if result is not None
    ]

    notes: list[str] = []
    if facts.errors:
        notes.append(f"часть данных недоступна ({'; '.join(facts.errors)})")
    if facts.open_truncated or facts.closed_truncated:
        notes.append("список задач прочитан не полностью")
    if facts.skipped_count:
        notes.append(f"{facts.skipped_count} задач пропущено из-за неполных данных")

    if not measured:
        summary = f"Задачи есть ({facts.total_count}), но ни одна метрика не применима."
        if notes:
            summary = f"{summary} Причины: {'; '.join(notes)}."
        return _unmeasured_result(facts, summary)

    metrics = tuple(metric for metric, _ in measured)
    total_weight = sum(METRIC_WEIGHTS[metric.code] for metric in metrics)
    score = sum(METRIC_WEIGHTS[metric.code] * value for metric, value in measured) / total_weight

    summary_parts = [metric.summary for metric in metrics]
    summary_parts.extend(notes)

    return CategoryResult(
        category=CATEGORY_CODE,
        status=DataStatus.MEASURED,
        score=score,
        summary="; ".join(summary_parts),
        metrics=metrics,
        recommendations=_build_recommendations(facts, context, metrics),
    )
