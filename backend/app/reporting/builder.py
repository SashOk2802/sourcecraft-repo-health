"""Преобразование результатов анализа в формат API и Markdown-отчёт."""

from __future__ import annotations

from datetime import datetime
from types import MappingProxyType

from backend.app.analysis.runner import AnalysisExecution
from backend.app.contracts import CategoryResult, Evidence, MetricResult, Recommendation
from backend.app.scoring.engine import CategoryContribution, ScoreLimit
from backend.app.scoring.methodology import CATEGORY_LABELS

_STATUS_LABELS = MappingProxyType(
    {
        "measured": "рассчитано",
        "unavailable": "нет данных",
        "not_applicable": "неприменимо",
        "insufficient_sample": "недостаточно данных",
        "error": "ошибка анализа",
    }
)


def build_report_payload(
    execution: AnalysisExecution,
    *,
    analysis_id: str,
) -> dict[str, object]:
    """Строит JSON-совместимую модель завершённого анализа для HTTP API."""

    if not analysis_id.strip():
        raise ValueError("analysis_id must not be empty")

    analysis = execution.analysis
    score_summary = execution.score_summary
    contributions = {item.category: item for item in score_summary.categories}

    return {
        "repository": {
            "id": analysis.repository.id,
            "organizationSlug": analysis.repository.organization_slug,
            "repositorySlug": analysis.repository.repository_slug,
            "name": f"{analysis.repository.organization_slug}/{analysis.repository.repository_slug}",
            "url": analysis.repository.web_url,
        },
        "analysis": {
            "id": analysis_id,
            "status": "partial" if score_summary.is_preliminary else "completed",
            "analyzedAt": _format_timestamp(analysis.analyzed_at),
            "commitSha": analysis.commit_sha,
            "methodologyVersion": analysis.methodology_version,
            "coverage": score_summary.coverage,
            "isPreliminary": score_summary.is_preliminary,
            "scoreLimit": _score_limit_payload(
                score_summary.score_limit, score_summary.uncapped_score
            ),
        },
        "score": analysis.score,
        "scoreDetails": {
            "measuredWeight": score_summary.measured_weight,
            "applicableWeight": score_summary.applicable_weight,
        },
        "categories": [
            _category_payload(category, contributions[category.category])
            for category in analysis.categories
        ],
        "recommendations": [
            _recommendation_payload(recommendation)
            for recommendation in analysis.recommendations
        ],
    }


def render_markdown_report(
    execution: AnalysisExecution,
    *,
    analysis_id: str,
) -> str:
    """Создаёт детерминированный Markdown-отчёт из модели завершённого анализа."""

    report = build_report_payload(execution, analysis_id=analysis_id)
    repository = report["repository"]
    analysis = report["analysis"]
    categories = report["categories"]
    recommendations = report["recommendations"]

    lines = [
        f"# Repo Health: {repository['name']}",
        "",
        f"Анализ `{analysis['id']}` от {analysis['analyzedAt']}.",
        f"Коммит: `{analysis['commitSha']}`. Методика: {analysis['methodologyVersion']}.",
        "",
        "## Итоговая оценка",
        "",
        f"**Repo Health Score: {_format_number(report['score'])} / 100**",
        f"Покрытие данных: {_format_percentage(analysis['coverage'])}.",
        f"Предварительная оценка: {'да' if analysis['isPreliminary'] else 'нет'}.",
    ]

    score_limit = analysis["scoreLimit"]
    if score_limit is not None:
        lines.extend(
            (
                "",
                "### Ограничение Score",
                "",
                score_limit["summary"],
                (
                    f"Без ограничения: {_format_number(score_limit['uncappedScore'])}; "
                    f"максимальный Score: {_format_number(score_limit['value'])}."
                ),
            )
        )

    lines.extend(("", "## Категории"))
    for category in categories:
        lines.extend(
            (
                "",
                f"### {category['label']}",
                "",
                f"- Статус: {_STATUS_LABELS[category['status']]}",
                f"- Оценка: {_format_number(category['score'])} / 100",
                f"- Базовый вес: {_format_number(category['weight'])} %",
                f"- Фактический вес: {_format_number(category['effectiveWeight'])} %",
                f"- Вклад в Score: {_format_number(category['points'])}",
                f"- {category['summary']}",
            )
        )
        if category["effectiveWeight"] is None:
            lines.append("- Не участвует в расчёте.")
        if category["reason"]:
            lines.append(f"- Причина: `{category['reason']}`")
        for fact in category["evidence"]:
            lines.append(f"- Факт `{fact['code']}`: {fact['summary']}")
            for evidence in fact["evidence"]:
                lines.append(_evidence_markdown(evidence))

    lines.extend(("", "## Рекомендации"))
    if not recommendations:
        lines.extend(("", "Рекомендаций пока нет."))
    else:
        for recommendation in recommendations:
            lines.extend(
                (
                    "",
                    f"### {recommendation['priority'].upper()}: {recommendation['action']}",
                    "",
                    f"Проблема: {recommendation['problem']}",
                    f"Почему: {recommendation['rationale']}",
                )
            )
            if recommendation["expectedEffect"]:
                lines.append(f"Ожидаемый эффект: {recommendation['expectedEffect']}")
            if recommendation["expectedScoreDelta"] is not None:
                lines.append(
                    "Ожидаемое изменение Score: "
                    f"+{_format_number(recommendation['expectedScoreDelta'])}."
                )
            if recommendation.get("aiActionPlan"):
                lines.extend(
                    (
                        "",
                        "**AI-план действий:**",
                        "",
                        recommendation["aiActionPlan"],
                    )
                )
            for evidence in recommendation["evidence"]:
                lines.append(_evidence_markdown(evidence))

        lines.extend(
            (
                "",
                (
                    "Возможные приросты не суммируются: рекомендации могут влиять на одни и те же "
                    "метрики или снять общее ограничение Score."
                ),
            )
        )

    return "\n".join(lines) + "\n"


def _category_payload(category: CategoryResult, contribution: CategoryContribution) -> dict[str, object]:
    return {
        "code": category.category,
        "label": CATEGORY_LABELS[category.category],
        "status": category.status.value,
        "score": category.score,
        "weight": contribution.weight,
        "effectiveWeight": contribution.effective_weight,
        "points": contribution.points,
        "summary": category.summary,
        "reason": category.reason,
        "evidence": [_metric_payload(metric) for metric in category.metrics],
    }


def _metric_payload(metric: MetricResult) -> dict[str, object]:
    return {
        "code": metric.code,
        "value": metric.value,
        "normalizedScore": metric.normalized_score,
        "summary": metric.summary,
        "evidence": [_evidence_payload(item) for item in metric.evidence],
    }


def _recommendation_payload(recommendation: Recommendation) -> dict[str, object]:
    return {
        "code": recommendation.code,
        "priority": recommendation.priority.value,
        "problem": recommendation.problem,
        "action": recommendation.action,
        "rationale": recommendation.rationale,
        "expectedEffect": recommendation.expected_effect,
        "expectedScoreDelta": recommendation.expected_score_delta,
        "evidence": [_evidence_payload(item) for item in recommendation.evidence],
        "aiActionPlan": recommendation.ai_action_plan,
    }


def _evidence_payload(evidence: Evidence) -> dict[str, object]:
    return {
        "source": evidence.source,
        "reference": evidence.reference,
        "summary": evidence.summary,
        "url": evidence.url,
    }


def _score_limit_payload(
    score_limit: ScoreLimit | None,
    uncapped_score: float | None,
) -> dict[str, object] | None:
    if score_limit is None:
        return None
    return {
        "value": score_limit.maximum_score,
        "uncappedScore": uncapped_score,
        "code": score_limit.code,
        "summary": score_limit.summary,
    }


def _format_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _format_number(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, int | float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _format_percentage(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, int | float):
        return _format_number(value * 100) + " %"
    return str(value)


def _evidence_markdown(evidence: dict[str, object]) -> str:
    label = f"{evidence['source']}: {evidence['reference']}"
    if evidence["url"]:
        return f"- [{label}]({evidence['url']}) — {evidence['summary']}"
    return f"- {label} — {evidence['summary']}"