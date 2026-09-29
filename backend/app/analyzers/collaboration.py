"""Бонусные показатели со звёздочкой: bus factor и качество review.

Не входят в Score v2 и не меняют веса категорий. Email авторов в отчёт
не попадают. Ошибка сбора review не роняет категорию Activity.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from backend.app.analyzers.activity import ActivityFacts, PullFact
from backend.app.contracts import (
    AnalysisContext,
    DataStatus,
    Evidence,
    InsightResult,
    RepositoryRef,
)
from backend.app.integrations.git_repository import CommitRecord
from backend.app.integrations.sourcecraft import SourceCraftClient, SourceCraftClientError

logger = logging.getLogger(__name__)

EVIDENCE_SOURCE = "sourcecraft-collaboration"

BUS_FACTOR_CODE = "bus_factor"
REVIEW_QUALITY_CODE = "review_quality"

BUS_FACTOR_LABEL = "Bus factor"
REVIEW_QUALITY_LABEL = "Качество review"

# Наименьшее число авторов, покрывающих хотя бы половину не-merge коммитов.
BUS_FACTOR_COVERAGE = 0.5

# Бюджет запросов merge-checks: один запрос на MR.
REVIEW_SAMPLE_LIMIT = 20

INSIGHT_ORDER = (BUS_FACTOR_CODE, REVIEW_QUALITY_CODE)


@dataclass(frozen=True, slots=True)
class MergeCheckFact:
    """Обезличенный итог code review по одному смерженному MR."""

    pull_slug: str
    review_disabled: bool
    total_approves: int | None
    error: str | None = None


def evaluate_bus_factor(
    commits: Sequence[CommitRecord],
    *,
    history_collected: bool = True,
    history_error: str | None = None,
    history_truncated: bool = False,
) -> InsightResult:
    """Считает bus factor по не-merge коммитам с известным автором.

    Merge-коммиты (два и больше родителя) отбрасываются. Обрыв истории или
    ошибка git не дают измеренного значения.
    """

    if history_error is not None:
        return InsightResult(
            code=BUS_FACTOR_CODE,
            label=BUS_FACTOR_LABEL,
            status=DataStatus.UNAVAILABLE,
            summary="Не удалось прочитать историю коммитов для bus factor.",
            reason=history_error,
        )
    if not history_collected:
        return InsightResult(
            code=BUS_FACTOR_CODE,
            label=BUS_FACTOR_LABEL,
            status=DataStatus.UNAVAILABLE,
            summary="История коммитов для bus factor не запрашивалась.",
            reason="commit_history_not_collected",
        )
    if history_truncated:
        return InsightResult(
            code=BUS_FACTOR_CODE,
            label=BUS_FACTOR_LABEL,
            status=DataStatus.INSUFFICIENT_SAMPLE,
            summary="История коммитов оборвана бюджетом: bus factor ненадёжен.",
            reason="commit_history_truncated",
        )

    counted = [
        record.author_email
        for record in commits
        if record.parent_count < 2 and record.author_email
    ]
    if not counted:
        if not commits:
            return InsightResult(
                code=BUS_FACTOR_CODE,
                label=BUS_FACTOR_LABEL,
                status=DataStatus.NOT_APPLICABLE,
                summary="За период нет коммитов: bus factor оценивать не на чем.",
                reason="no_commits_in_period",
            )
        return InsightResult(
            code=BUS_FACTOR_CODE,
            label=BUS_FACTOR_LABEL,
            status=DataStatus.INSUFFICIENT_SAMPLE,
            summary="В истории нет не-merge коммитов с автором для bus factor.",
            reason="no_authored_non_merge_commits",
        )

    counts = Counter(counted)
    total = sum(counts.values())
    ordered = counts.most_common()
    covered = 0
    bus_factor = 0
    for _email, commit_count in ordered:
        covered += commit_count
        bus_factor += 1
        if covered / total >= BUS_FACTOR_COVERAGE:
            break

    top_share = ordered[0][1] / total
    detail = (
        f"Самый активный автор дал {top_share:.0%} не-merge коммитов "
        f"({ordered[0][1]} из {total})."
    )
    action = None
    if bus_factor == 1:
        action = (
            "Распределите знание о коде: сейчас половина коммитов идёт "
            "от одного автора."
        )

    return InsightResult(
        code=BUS_FACTOR_CODE,
        label=BUS_FACTOR_LABEL,
        status=DataStatus.MEASURED,
        value=bus_factor,
        summary=(
            f"Bus factor {bus_factor}: столько авторов покрывают "
            f"не менее {BUS_FACTOR_COVERAGE:.0%} не-merge коммитов за период."
        ),
        detail=detail,
        action=action,
        evidence=(
            Evidence(
                source=EVIDENCE_SOURCE,
                reference="commit-authors",
                summary=(
                    f"Учтено {total} не-merge коммитов от {len(counts)} авторов; "
                    f"merge-коммиты не входят."
                ),
            ),
        ),
    )


def collect_merge_checks(
    client: SourceCraftClient,
    repository: RepositoryRef,
    pulls: Sequence[PullFact],
    context: AnalysisContext,
    *,
    sample_limit: int = REVIEW_SAMPLE_LIMIT,
) -> tuple[MergeCheckFact, ...]:
    """Читает merge-checks для до ``sample_limit`` свежих смерженных MR периода."""

    sample = _merged_pulls_sample(pulls, context, sample_limit=sample_limit)
    if not sample:
        return ()

    org = urllib.parse.quote(repository.organization_slug, safe="")
    repo = urllib.parse.quote(repository.repository_slug, safe="")
    facts: list[MergeCheckFact] = []
    for pull in sample:
        slug = urllib.parse.quote(pull.slug, safe="")
        path = f"/repos/{org}/{repo}/pulls/{slug}/merge-checks"
        try:
            payload = client.get_json(path)
        except SourceCraftClientError as error:
            facts.append(
                MergeCheckFact(
                    pull_slug=pull.slug,
                    review_disabled=False,
                    total_approves=None,
                    error=str(error),
                )
            )
            continue
        facts.append(_parse_merge_check(pull.slug, payload))
    return tuple(facts)


def evaluate_review_quality(
    checks: Sequence[MergeCheckFact],
    *,
    pulls_error: str | None = None,
    pulls_truncated: bool = False,
    merged_in_period: int = 0,
    sample_limit: int = REVIEW_SAMPLE_LIMIT,
) -> InsightResult:
    """Доля смерженных MR, где code review включён и есть хотя бы один approve."""

    if pulls_error is not None:
        return InsightResult(
            code=REVIEW_QUALITY_CODE,
            label=REVIEW_QUALITY_LABEL,
            status=DataStatus.UNAVAILABLE,
            summary="Не удалось получить список MR для оценки review.",
            reason="pulls_unavailable",
        )
    if pulls_truncated:
        return InsightResult(
            code=REVIEW_QUALITY_CODE,
            label=REVIEW_QUALITY_LABEL,
            status=DataStatus.INSUFFICIENT_SAMPLE,
            summary="Список MR оборван: долю review по неполной выборке не считаем.",
            reason="pulls_truncated",
        )
    if merged_in_period == 0:
        return InsightResult(
            code=REVIEW_QUALITY_CODE,
            label=REVIEW_QUALITY_LABEL,
            status=DataStatus.NOT_APPLICABLE,
            summary="За период нет смерженных MR: качество review оценивать не на чем.",
            reason="no_merged_mrs_in_period",
        )
    if not checks:
        return InsightResult(
            code=REVIEW_QUALITY_CODE,
            label=REVIEW_QUALITY_LABEL,
            status=DataStatus.UNAVAILABLE,
            summary="Не удалось прочитать merge-checks смерженных MR.",
            reason="merge_checks_empty",
        )

    if any(item.error is not None for item in checks):
        return InsightResult(
            code=REVIEW_QUALITY_CODE,
            label=REVIEW_QUALITY_LABEL,
            status=DataStatus.UNAVAILABLE,
            summary=(
                "У части смерженных MR не удалось прочитать статус review; "
                "исторические approve могли быть недоступны."
            ),
            reason="merge_checks_error",
            evidence=(
                Evidence(
                    source=EVIDENCE_SOURCE,
                    reference="merge-checks",
                    summary=(
                        f"Ошибок чтения: "
                        f"{sum(1 for item in checks if item.error is not None)} "
                        f"из {len(checks)}."
                    ),
                ),
            ),
        )

    if any(item.total_approves is None and not item.review_disabled for item in checks):
        return InsightResult(
            code=REVIEW_QUALITY_CODE,
            label=REVIEW_QUALITY_LABEL,
            status=DataStatus.UNAVAILABLE,
            summary=(
                "Merge-checks не отдали число approve по смерженным MR; "
                "это не считается нулём ревью."
            ),
            reason="merge_checks_approves_missing",
        )

    enabled = tuple(item for item in checks if not item.review_disabled)
    if not enabled:
        return InsightResult(
            code=REVIEW_QUALITY_CODE,
            label=REVIEW_QUALITY_LABEL,
            status=DataStatus.NOT_APPLICABLE,
            summary="Code review выключен у всех проверенных смерженных MR.",
            reason="code_review_disabled",
        )

    approved = sum(1 for item in enabled if (item.total_approves or 0) >= 1)
    ratio = approved / len(enabled)
    sample_note = ""
    if merged_in_period > sample_limit:
        sample_note = (
            f" Проверены {len(checks)} из {merged_in_period} свежих смерженных MR."
        )

    action = None
    if ratio < 0.5:
        action = (
            "Включите обязательный code review и не мержите без хотя бы "
            "одного approve."
        )

    return InsightResult(
        code=REVIEW_QUALITY_CODE,
        label=REVIEW_QUALITY_LABEL,
        status=DataStatus.MEASURED,
        value=round(ratio, 4),
        summary=(
            f"У {approved} из {len(enabled)} смерженных MR с включённым review "
            f"есть хотя бы один approve ({ratio:.0%}).{sample_note}"
        ),
        detail=(
            f"Выборка: {len(checks)} MR; review выключен у "
            f"{len(checks) - len(enabled)}."
        ),
        action=action,
        evidence=(
            Evidence(
                source=EVIDENCE_SOURCE,
                reference="merge-checks",
                summary=(
                    f"Approve >= 1: {approved}; включённый review: {len(enabled)}; "
                    f"выборка: {len(checks)}."
                ),
            ),
        ),
    )


def build_collaboration_insights(
    facts: ActivityFacts,
    context: AnalysisContext,
    *,
    merge_checks: Sequence[MergeCheckFact] = (),
) -> tuple[InsightResult, ...]:
    """Собирает bus factor и review quality из уже известных фактов Activity."""

    history = facts.commit_history
    bus = evaluate_bus_factor(
        history.commits,
        history_collected=history.collected,
        history_error=history.error,
        history_truncated=history.truncated,
    )

    merged_in_period = sum(
        1
        for pull in facts.pulls
        if pull.is_merged and context.period_start <= pull.updated_at <= context.period_end
    )
    review = evaluate_review_quality(
        merge_checks,
        pulls_error=facts.pulls_error,
        pulls_truncated=facts.pulls_truncated,
        merged_in_period=merged_in_period,
    )
    return (bus, review)


def _merged_pulls_sample(
    pulls: Sequence[PullFact],
    context: AnalysisContext,
    *,
    sample_limit: int,
) -> tuple[PullFact, ...]:
    merged = [
        pull
        for pull in pulls
        if pull.is_merged
        and pull.slug
        and context.period_start <= pull.updated_at <= context.period_end
    ]
    merged.sort(key=lambda item: item.updated_at, reverse=True)
    return tuple(merged[:sample_limit])


def _parse_merge_check(pull_slug: str, payload: object) -> MergeCheckFact:
    if not isinstance(payload, dict):
        return MergeCheckFact(
            pull_slug=pull_slug,
            review_disabled=False,
            total_approves=None,
            error="merge_checks_not_object",
        )
    code_review = payload.get("code_review")
    if code_review is None:
        return MergeCheckFact(
            pull_slug=pull_slug,
            review_disabled=False,
            total_approves=None,
            error="code_review_missing",
        )
    if not isinstance(code_review, dict):
        return MergeCheckFact(
            pull_slug=pull_slug,
            review_disabled=False,
            total_approves=None,
            error="code_review_not_object",
        )
    disabled = bool(code_review.get("disabled"))
    raw_approves = code_review.get("total_approves")
    if raw_approves is None:
        return MergeCheckFact(
            pull_slug=pull_slug,
            review_disabled=disabled,
            total_approves=None,
        )
    try:
        total_approves = int(raw_approves)
    except (TypeError, ValueError):
        return MergeCheckFact(
            pull_slug=pull_slug,
            review_disabled=disabled,
            total_approves=None,
            error="total_approves_invalid",
        )
    return MergeCheckFact(
        pull_slug=pull_slug,
        review_disabled=disabled,
        total_approves=total_approves,
    )
