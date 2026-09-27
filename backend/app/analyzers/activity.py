"""Категория Activity: развивается ли проект, а не сколько у него коммитов.

Категория смотрит на давность обновления, активные недели по истории Git,
merge requests, релизы и число участников. Сырой объём не должен безгранично
поднимать оценку: три недавних merge дают тот же вклад, что и семьдесят,
а девятая активная неделя не лучше восьмой.

Модуль разделён на две части. `collect` обращается к SourceCraft и возвращает факты.
Историю коммитов читает отдельный `collect_commit_history`: ошибка git не выбрасывается
наружу и не обнуляет уже собранные API-метрики. `evaluate` — чистая функция: она не
ходит в сеть и не смотрит на системное время.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from backend.app.integrations.git_repository import GitCloneError, read_commit_timestamps
from backend.app.integrations.sourcecraft import SourceCraftClient, SourceCraftClientError

logger = logging.getLogger(__name__)

CATEGORY_CODE = "activity"

EVIDENCE_SOURCE = "sourcecraft-activity"

# Давность «хорошо / плохо». 14 дней — живой проект; 180 дней — за пределами
# обычного периода анализа и уже не «редко меняющийся зрелый репозиторий».
RECENCY_BEST_DAYS = 14.0
RECENCY_WORST_DAYS = 180.0
RECENCY_STALE_DAYS = 90

# Потолок: дальше этого числа оценка не растёт. Иначе 80 MR выглядят «здоровее»
# трёх недавних merge только из-за объёма.
MERGED_MR_CAP = 3
RELEASE_CAP = 3
CONTRIBUTOR_CAP = 3

# Восемь недель за 180 дней — уже регулярная работа. Дальше объём коммитов
# не повышает оценку: большой поток не должен выглядеть здоровее спокойного.
ACTIVE_WEEKS_CAP = 8
ACTIVE_WEEKS_BEST = 8.0
COMMIT_HISTORY_LIMIT = 20_000
COMMIT_HISTORY_TIMEOUT_SECONDS = 90.0

METRIC_WEIGHTS = {
    "last_activity_days": 40.0,
    "active_weeks_in_period": 25.0,
    "merged_mr_in_period": 25.0,
    "releases_in_period": 20.0,
    "contributor_count": 15.0,
}

STATUS_MERGED = "merged"
STATUS_PUBLISHED = "published"

PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 30


@dataclass(frozen=True, slots=True)
class PullFact:
    """Минимум сведений об одном merge request."""

    slug: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime

    @property
    def is_merged(self) -> bool:
        return self.status == STATUS_MERGED


@dataclass(frozen=True, slots=True)
class ReleaseFact:
    """Опубликованный или черновой релиз."""

    tag: str
    title: str
    released_at: datetime
    status: str
    is_pre_release: bool

    @property
    def is_published(self) -> bool:
        return self.status == STATUS_PUBLISHED


@dataclass(frozen=True, slots=True)
class ContributorFact:
    """Участник из списка contributors. Числа коммитов API не отдаёт."""

    identity: str
    username: str


@dataclass(frozen=True, slots=True)
class CommitHistoryFacts:
    """История коммитов. ``collected=False`` значит, что её не запрашивали.

    Неполная или упавшая история не превращается в ноль баллов: метрика просто
    не участвует, а остальные источники сохраняют свою оценку.
    """

    collected: bool = False
    committed_at: tuple[datetime, ...] = ()
    truncated: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ActivityFacts:
    """Собранные факты и полнота каждого источника."""

    last_updated: datetime | None = None
    is_empty: bool = False
    repository_error: str | None = None
    contributors: tuple[ContributorFact, ...] = ()
    pulls: tuple[PullFact, ...] = ()
    releases: tuple[ReleaseFact, ...] = ()
    contributors_truncated: bool = False
    pulls_truncated: bool = False
    releases_truncated: bool = False
    contributors_error: str | None = None
    pulls_error: str | None = None
    releases_error: str | None = None
    skipped_count: int = 0
    commit_history: CommitHistoryFacts = field(default_factory=CommitHistoryFacts)

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(
            error
            for error in (
                self.repository_error,
                self.contributors_error,
                self.pulls_error,
                self.releases_error,
            )
            if error
        )


def parse_datetime(value: Any) -> datetime | None:
    """Разбирает метку времени RFC3339 и приводит её к UTC."""
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


def parse_pull(raw: Any) -> PullFact | None:
    if not isinstance(raw, dict):
        return None
    created_at = parse_datetime(raw.get("created_at"))
    updated_at = parse_datetime(raw.get("updated_at"))
    if created_at is None or updated_at is None:
        return None
    return PullFact(
        slug=str(raw.get("slug") or raw.get("id") or ""),
        title=str(raw.get("title") or ""),
        status=str(raw.get("status") or ""),
        created_at=created_at,
        updated_at=updated_at,
    )


def parse_release(raw: Any) -> ReleaseFact | None:
    if not isinstance(raw, dict):
        return None
    released_at = parse_datetime(raw.get("released_at")) or parse_datetime(raw.get("created_at"))
    if released_at is None:
        return None
    return ReleaseFact(
        tag=str(raw.get("tag") or raw.get("id") or ""),
        title=str(raw.get("title") or raw.get("tag") or ""),
        released_at=released_at,
        status=str(raw.get("status") or ""),
        is_pre_release=bool(raw.get("is_pre_release")),
    )


def parse_contributor(raw: Any) -> ContributorFact | None:
    if not isinstance(raw, dict):
        return None
    identity = str(raw.get("id") or raw.get("username") or "")
    username = str(raw.get("username") or raw.get("display_name") or identity)
    if not identity:
        return None
    return ContributorFact(identity=identity, username=username)


def _parse_many(items: Iterable[Any], parser) -> tuple[tuple[Any, ...], int]:
    parsed: list[Any] = []
    skipped = 0
    for item in items:
        fact = parser(item)
        if fact is None:
            skipped += 1
        else:
            parsed.append(fact)
    return tuple(parsed), skipped


def build_facts(
    *,
    last_updated: datetime | None = None,
    is_empty: bool = False,
    repository_error: str | None = None,
    contributor_items: Iterable[Any] = (),
    pull_items: Iterable[Any] = (),
    release_items: Iterable[Any] = (),
    contributors_truncated: bool = False,
    pulls_truncated: bool = False,
    releases_truncated: bool = False,
    contributors_error: str | None = None,
    pulls_error: str | None = None,
    releases_error: str | None = None,
    commit_history: CommitHistoryFacts | None = None,
) -> ActivityFacts:
    """Собирает факты из сырых ответов API или из сохранённых фикстур."""
    contributors, skipped_contributors = _parse_many(contributor_items, parse_contributor)
    pulls, skipped_pulls = _parse_many(pull_items, parse_pull)
    releases, skipped_releases = _parse_many(release_items, parse_release)
    return ActivityFacts(
        last_updated=last_updated,
        is_empty=is_empty,
        repository_error=repository_error,
        contributors=contributors,
        pulls=pulls,
        releases=releases,
        contributors_truncated=contributors_truncated,
        pulls_truncated=pulls_truncated,
        releases_truncated=releases_truncated,
        contributors_error=contributors_error,
        pulls_error=pulls_error,
        releases_error=releases_error,
        skipped_count=skipped_contributors + skipped_pulls + skipped_releases,
        commit_history=commit_history or CommitHistoryFacts(),
    )


def _fetch_pages(
    client: SourceCraftClient,
    path: str,
    items_key: str,
    *,
    max_pages: int,
) -> tuple[list[Any], bool]:
    """Обходит страницы списка и отличает конец выборки от оборванного бюджета.

    Общий ``get_paginated_objects`` при исчерпании страниц бросает ошибку и
    отвергает элемент, который не является объектом. Здесь обрыв бюджета — это
    ``truncated``, а битая запись позже попадает в ``skipped_count``. Повтор
    того же ``next_page_token`` считается ошибкой источника, а не новой страницей.
    """
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    params: dict[str, str | int] = {"page_size": PAGE_SIZE}
    items: list[Any] = []
    seen_tokens: set[str] = set()

    for page in range(1, max_pages + 1):
        payload = client.get_json(path, params=params)
        if not isinstance(payload, dict):
            raise SourceCraftClientError("SourceCraft activity response must be an object")

        items.extend(payload.get(items_key) or [])
        next_token = payload.get("next_page_token") or ""
        if not next_token:
            return items, False
        if not isinstance(next_token, str):
            raise SourceCraftClientError("SourceCraft next_page_token must be a string")
        if page == max_pages:
            return items, True
        if next_token in seen_tokens:
            raise SourceCraftClientError("SourceCraft returned a repeated next_page_token")
        seen_tokens.add(next_token)
        params["page_token"] = next_token

    return items, True


def _fetch_list(
    client: SourceCraftClient,
    path: str,
    items_key: str,
    *,
    max_pages: int,
) -> tuple[list[Any], bool, str | None]:
    try:
        items, truncated = _fetch_pages(client, path, items_key, max_pages=max_pages)
    except SourceCraftClientError as error:
        return [], False, str(error)
    return items, truncated, None


def _fetch_repository(
    client: SourceCraftClient,
    path: str,
) -> tuple[datetime | None, bool, str | None]:
    try:
        payload = client.get_json(path)
    except SourceCraftClientError as error:
        return None, False, str(error)
    if not isinstance(payload, dict):
        return None, False, "SourceCraft repository response must be an object"
    return parse_datetime(payload.get("last_updated")), bool(payload.get("is_empty")), None


def collect_commit_history(
    client: SourceCraftClient,
    context: AnalysisContext,
) -> CommitHistoryFacts:
    """Читает историю коммитов. Любой сбой остаётся фактом, а не исключением."""

    repository = context.repository
    try:
        repo_url = SourceCraftClient.resolve_git_clone_url(
            repository.organization_slug,
            repository.repository_slug,
            repository.web_url,
        )
        page = read_commit_timestamps(
            repo_url,
            since=context.period_start,
            until=context.period_end,
            revision=context.commit_sha,
            auth_token=client.git_bearer_token(),
            max_commits=COMMIT_HISTORY_LIMIT,
            timeout_seconds=COMMIT_HISTORY_TIMEOUT_SECONDS,
        )
    except SourceCraftClientError as error:
        return CommitHistoryFacts(collected=True, error=str(error))
    except GitCloneError as error:
        return CommitHistoryFacts(collected=True, error=str(error))
    except Exception:
        logger.exception("Не удалось прочитать историю коммитов.")
        return CommitHistoryFacts(collected=True, error="не удалось прочитать историю коммитов")

    return CommitHistoryFacts(
        collected=True,
        committed_at=page.committed_at,
        truncated=page.truncated,
    )


def collect(
    client: SourceCraftClient,
    repository: RepositoryRef,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    commit_history: CommitHistoryFacts | None = None,
) -> ActivityFacts:
    """Обращается к SourceCraft и возвращает факты. Оценок не выставляет."""
    org = urllib.parse.quote(repository.organization_slug, safe="")
    repo = urllib.parse.quote(repository.repository_slug, safe="")
    base = f"/repos/{org}/{repo}"

    last_updated, is_empty, repository_error = _fetch_repository(client, base)
    contributors, contributors_truncated, contributors_error = _fetch_list(
        client, f"{base}/contributors", "contributors", max_pages=max_pages
    )
    pulls, pulls_truncated, pulls_error = _fetch_list(
        client, f"{base}/pulls", "pull_requests", max_pages=max_pages
    )
    releases, releases_truncated, releases_error = _fetch_list(
        client, f"{base}/releases", "releases", max_pages=max_pages
    )

    return build_facts(
        last_updated=last_updated,
        is_empty=is_empty,
        repository_error=repository_error,
        contributor_items=contributors,
        pull_items=pulls,
        release_items=releases,
        contributors_truncated=contributors_truncated,
        pulls_truncated=pulls_truncated,
        releases_truncated=releases_truncated,
        contributors_error=contributors_error,
        pulls_error=pulls_error,
        releases_error=releases_error,
        commit_history=commit_history,
    )


def _linear_score(value: float, *, best: float, worst: float) -> float:
    if best == worst:
        raise ValueError("best and worst thresholds must differ")
    ratio = (value - worst) / (best - worst)
    return max(0.0, min(1.0, ratio)) * 100


def _in_period(moment: datetime, context: AnalysisContext) -> bool:
    return context.period_start <= moment <= context.period_end


def _days_since(context: AnalysisContext, moment: datetime) -> int:
    return max(0, (context.analyzed_at - moment).days)


def _repo_url(repository: RepositoryRef, suffix: str) -> str | None:
    if not repository.web_url:
        return None
    return f"{repository.web_url.rstrip('/')}/{suffix.lstrip('/')}"


def _recency_metric(
    facts: ActivityFacts, context: AnalysisContext
) -> tuple[MetricResult, float] | None:
    if facts.repository_error or facts.last_updated is None:
        return None

    days = _days_since(context, facts.last_updated)
    score = _linear_score(float(days), best=RECENCY_BEST_DAYS, worst=RECENCY_WORST_DAYS)
    evidence = ()
    url = _repo_url(context.repository, "")
    if url:
        evidence = (
            Evidence(
                source=EVIDENCE_SOURCE,
                reference="last_updated",
                summary=(
                    f"последнее обновление репозитория "
                    f"{facts.last_updated.date().isoformat()} ({days} дн. назад)"
                ),
                url=url.rstrip("/"),
            ),
        )
    metric = MetricResult(
        code="last_activity_days",
        value=days,
        normalized_score=score,
        summary=f"последняя активность репозитория {days} дн. назад",
        evidence=evidence,
    )
    return metric, score


def _merged_mr_metric(
    facts: ActivityFacts, context: AnalysisContext
) -> tuple[MetricResult, float] | None:
    if facts.pulls_error or facts.pulls_truncated:
        return None
    if not facts.pulls:
        return None

    merged = [
        pull
        for pull in facts.pulls
        if pull.is_merged and _in_period(pull.updated_at, context)
    ]
    capped = min(len(merged), MERGED_MR_CAP)
    score = _linear_score(float(capped), best=float(MERGED_MR_CAP), worst=0.0)
    evidence = tuple(
        Evidence(
            source=EVIDENCE_SOURCE,
            reference=pull.slug,
            summary=f"«{pull.title}» смержен {pull.updated_at.date().isoformat()}",
            url=_repo_url(context.repository, f"pr/{urllib.parse.quote(pull.slug, safe='')}"),
        )
        for pull in sorted(merged, key=lambda item: item.updated_at, reverse=True)[:5]
    )
    summary = f"за период смержено {len(merged)} MR"
    if len(merged) > MERGED_MR_CAP:
        summary += f" (в оценке учитываются первые {MERGED_MR_CAP})"
    metric = MetricResult(
        code="merged_mr_in_period",
        value=len(merged),
        normalized_score=score,
        summary=summary,
        evidence=evidence,
    )
    return metric, score


def _release_metric(
    facts: ActivityFacts, context: AnalysisContext
) -> tuple[MetricResult, float] | None:
    if facts.releases_error or facts.releases_truncated:
        return None
    if not facts.releases:
        return None

    published = [
        release
        for release in facts.releases
        if release.is_published and _in_period(release.released_at, context)
    ]
    capped = min(len(published), RELEASE_CAP)
    score = _linear_score(float(capped), best=float(RELEASE_CAP), worst=0.0)
    evidence = tuple(
        Evidence(
            source=EVIDENCE_SOURCE,
            reference=release.tag,
            summary=f"релиз {release.title} {release.released_at.date().isoformat()}",
            url=_repo_url(
                context.repository, f"releases/{urllib.parse.quote(release.tag, safe='')}"
            ),
        )
        for release in sorted(published, key=lambda item: item.released_at, reverse=True)[:5]
    )
    summary = f"за период опубликовано {len(published)} релизов"
    if len(published) > RELEASE_CAP:
        summary += f" (в оценке учитываются первые {RELEASE_CAP})"
    metric = MetricResult(
        code="releases_in_period",
        value=len(published),
        normalized_score=score,
        summary=summary,
        evidence=evidence,
    )
    return metric, score


def _contributor_metric(facts: ActivityFacts) -> tuple[MetricResult, float] | None:
    if facts.contributors_error or facts.contributors_truncated:
        return None
    if not facts.contributors:
        # Пустой список нельзя отличить от «источник не вызывали»: ноль участников
        # не должен превращать отсутствие данных в оценку 0.
        return None

    count = len(facts.contributors)
    capped = min(count, CONTRIBUTOR_CAP)
    score = _linear_score(float(capped), best=float(CONTRIBUTOR_CAP), worst=0.0)
    metric = MetricResult(
        code="contributor_count",
        value=count,
        normalized_score=score,
        summary=f"в списке contributors {count} участников",
    )
    return metric, score


def _active_weeks_metric(
    facts: ActivityFacts, context: AnalysisContext
) -> tuple[MetricResult, float] | None:
    history = facts.commit_history
    if not history.collected or history.error or history.truncated:
        return None

    weeks: set[tuple[int, int]] = set()
    commit_count = 0
    for moment in history.committed_at:
        if not _in_period(moment, context):
            continue
        commit_count += 1
        iso = moment.astimezone(UTC).isocalendar()
        weeks.add((iso.year, iso.week))

    count = len(weeks)
    capped = min(count, ACTIVE_WEEKS_CAP)
    score = _linear_score(float(capped), best=ACTIVE_WEEKS_BEST, worst=0.0)
    summary = f"за период коммиты были в {count} неделях ({commit_count} коммитов)"
    if count > ACTIVE_WEEKS_CAP:
        summary += f" (в оценке учитываются первые {ACTIVE_WEEKS_CAP})"
    url = _repo_url(context.repository, "")
    evidence = (
        Evidence(
            source=EVIDENCE_SOURCE,
            reference="commit-history",
            summary=summary,
            url=url.rstrip("/") if url else None,
        ),
    )
    metric = MetricResult(
        code="active_weeks_in_period",
        value=count,
        normalized_score=score,
        summary=summary,
        evidence=evidence,
    )
    return metric, score


def _score_delta(metrics: Sequence[MetricResult], code: str, new_score: float) -> float:
    """На сколько пунктов вырастет категория, если одна метрика станет ``new_score``."""

    pairs = [
        (metric.code, float(metric.normalized_score))
        for metric in metrics
        if metric.normalized_score is not None
    ]
    if not pairs or all(metric_code != code for metric_code, _ in pairs):
        return 0.0

    def combined(items: list[tuple[str, float]]) -> float:
        total_weight = sum(METRIC_WEIGHTS[metric_code] for metric_code, _ in items)
        return sum(METRIC_WEIGHTS[metric_code] * value for metric_code, value in items) / total_weight

    before = combined(pairs)
    after = combined(
        [(metric_code, new_score if metric_code == code else value) for metric_code, value in pairs]
    )
    return max(0.0, after - before)


def _build_recommendations(
    facts: ActivityFacts,
    context: AnalysisContext,
    metrics: Sequence[MetricResult],
) -> tuple[Recommendation, ...]:
    by_code = {metric.code: metric for metric in metrics}
    recommendations: list[Recommendation] = []

    recency = by_code.get("last_activity_days")
    stale = (
        recency is not None
        and isinstance(recency.value, int)
        and recency.value > RECENCY_STALE_DAYS
    )
    if stale:
        recommendations.append(
            Recommendation(
                code="activity-stale-repository",
                priority=RecommendationPriority.P1,
                problem=recency.summary,
                action=(
                    "Верните регулярные изменения: даже небольшой релиз или merge "
                    "показывает, что проект жив."
                ),
                rationale=(
                    "Долгое отсутствие обновлений для открытого репозитория означает, "
                    "что им перестали пользоваться как рабочей кодовой базой."
                ),
                expected_effect="Свежая активность в пределах 14 дней поднимает метрику давности до 100.",
                expected_score_delta=_score_delta(metrics, "last_activity_days", 100.0),
                evidence=recency.evidence,
            )
        )

    merged = by_code.get("merged_mr_in_period")
    if (
        merged is not None
        and isinstance(merged.value, int)
        and merged.value == 0
        and facts.pulls
    ):
        recommendations.append(
            Recommendation(
                code="activity-mr-not-merged",
                priority=RecommendationPriority.P2,
                problem="За период ни один merge request не доведён до merge.",
                action=(
                    "Разберите открытые и отклонённые MR: смержьте готовые "
                    "или закройте неактуальные."
                ),
                rationale="Список MR без merge не заменяет содержательную поставку изменений.",
                expected_effect="Один смерженный MR в периоде поднимает метрику merge с нуля.",
                expected_score_delta=_score_delta(
                    metrics,
                    "merged_mr_in_period",
                    _linear_score(1.0, best=float(MERGED_MR_CAP), worst=0.0),
                ),
                evidence=merged.evidence,
            )
        )

    weeks = by_code.get("active_weeks_in_period")
    if (
        not stale
        and weeks is not None
        and isinstance(weeks.value, int)
        and weeks.value == 0
    ):
        recommendations.append(
            Recommendation(
                code="activity-no-commits-in-period",
                priority=RecommendationPriority.P2,
                problem="За период в истории зафиксированного коммита нет ни одного коммита.",
                action="Верните изменения в ветку по умолчанию, даже если это небольшой коммит.",
                rationale=(
                    "Обновление карточки репозитория без коммитов не показывает, "
                    "что код проекта продолжает развиваться."
                ),
                expected_effect=(
                    f"{ACTIVE_WEEKS_CAP} недель с коммитами за период поднимают "
                    "метрику активных недель до 100."
                ),
                expected_score_delta=_score_delta(metrics, "active_weeks_in_period", 100.0),
                evidence=weeks.evidence,
            )
        )

    releases = by_code.get("releases_in_period")
    if (
        releases is not None
        and isinstance(releases.value, int)
        and releases.value == 0
        and facts.releases
        and not recommendations
    ):
        recommendations.append(
            Recommendation(
                code="activity-no-recent-release",
                priority=RecommendationPriority.P3,
                problem="Релизы у проекта есть, но за период не опубликовано ни одного.",
                action="Если изменения накапливаются, оформите очередной релиз.",
                rationale=(
                    "Редкие релизы затрудняют потребителям понять, "
                    "можно ли брать проект в работу."
                ),
                expected_effect="Один опубликованный релиз в периоде поднимает метрику релизов с нуля.",
                expected_score_delta=_score_delta(
                    metrics,
                    "releases_in_period",
                    _linear_score(1.0, best=float(RELEASE_CAP), worst=0.0),
                ),
                evidence=_latest_release_evidence(facts, context),
            )
        )

    return tuple(recommendations)


def _latest_release_evidence(
    facts: ActivityFacts, context: AnalysisContext
) -> tuple[Evidence, ...]:
    published = [release for release in facts.releases if release.is_published]
    if not published:
        return ()
    latest = max(published, key=lambda release: release.released_at)
    return (
        Evidence(
            source=EVIDENCE_SOURCE,
            reference=latest.tag,
            summary=f"последний релиз {latest.title} {latest.released_at.date().isoformat()}",
            url=_repo_url(
                context.repository, f"releases/{urllib.parse.quote(latest.tag, safe='')}"
            ),
        ),
    )


def _unmeasured_result(facts: ActivityFacts, summary: str) -> CategoryResult:
    if facts.errors and not any(
        (
            facts.last_updated is not None,
            facts.contributors,
            facts.pulls,
            facts.releases,
        )
    ):
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.UNAVAILABLE,
            score=None,
            summary=summary,
            reason="; ".join(facts.errors),
        )

    if facts.pulls_truncated or facts.releases_truncated or facts.contributors_truncated:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.INSUFFICIENT_SAMPLE,
            score=None,
            summary=summary,
            reason="Списки активности прочитаны не полностью.",
        )

    return CategoryResult(
        category=CATEGORY_CODE,
        status=DataStatus.INSUFFICIENT_SAMPLE,
        score=None,
        summary=summary,
        reason="Недостаточно данных, чтобы измерить активность.",
    )


def evaluate(facts: ActivityFacts, context: AnalysisContext) -> CategoryResult:
    """Превращает факты в оценку категории. Без сети и без текущих часов."""
    if (
        facts.repository_error
        and facts.contributors_error
        and facts.pulls_error
        and facts.releases_error
    ):
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.UNAVAILABLE,
            score=None,
            summary="Не удалось получить данные об активности репозитория.",
            reason="; ".join(facts.errors),
        )

    if facts.is_empty:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.NOT_APPLICABLE,
            score=None,
            summary="Репозиторий пуст: активность оценивать не на чем.",
            reason="empty_repository",
        )

    measured: list[tuple[MetricResult, float]] = [
        result
        for result in (
            _recency_metric(facts, context),
            _active_weeks_metric(facts, context),
            _merged_mr_metric(facts, context),
            _release_metric(facts, context),
            _contributor_metric(facts),
        )
        if result is not None
    ]

    notes: list[str] = []
    if facts.errors:
        notes.append(f"часть данных недоступна ({'; '.join(facts.errors)})")
    if facts.pulls_truncated or facts.releases_truncated or facts.contributors_truncated:
        notes.append("список активности прочитан не полностью")
    if facts.commit_history.error:
        notes.append(f"история коммитов недоступна ({facts.commit_history.error})")
    elif facts.commit_history.truncated:
        notes.append("история коммитов прочитана не полностью")
    if facts.skipped_count:
        notes.append(f"{facts.skipped_count} записей пропущено из-за неполных данных")

    if not measured:
        summary = "Данные об активности есть, но ни одна метрика не применима."
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
