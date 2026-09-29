"""Обогащение детерминированных рекомендаций AI-планом действий из YandexGPT."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from backend.app.ai.client import YandexAiClient
from backend.app.analysis.runner import AnalysisExecution
from backend.app.contracts import AnalysisResult, CategoryResult, Recommendation

logger = logging.getLogger(__name__)

# Системный промпт — фиксированный, не меняется в зависимости от данных репозитория.
_SYSTEM_PROMPT = (
    "Ты — технический советник по качеству репозиториев. "
    "Тебе предоставлены ТОЛЬКО фактические данные автоматического анализа. "
    "Напиши конкретный пошаговый план устранения проблемы (3–5 шагов). "
    "Основывайся строго на предоставленных данных. Не придумывай факты. "
    "В ответе — только план, без вступления и заключения, на русском языке."
)


async def enrich_execution(
    execution: AnalysisExecution,
    *,
    client: YandexAiClient | None,
) -> AnalysisExecution:
    """Обогащает рекомендации AI-планом действий.

    Если client is None или вызов упал — возвращает оригинальный execution без изменений.
    Все вызовы к AI выполняются параллельно через asyncio.gather.
    """
    if client is None or not execution.analysis.recommendations:
        return execution

    analysis = execution.analysis
    enriched_recommendations = await _enrich_recommendations(
        analysis.recommendations,
        analysis,
        client=client,
    )

    if enriched_recommendations is analysis.recommendations:
        return execution

    enriched_analysis = AnalysisResult(
        repository=analysis.repository,
        analyzed_at=analysis.analyzed_at,
        commit_sha=analysis.commit_sha,
        categories=analysis.categories,
        score=analysis.score,
        methodology_version=analysis.methodology_version,
        recommendations=enriched_recommendations,
    )
    return AnalysisExecution(
        analysis=enriched_analysis,
        score_summary=execution.score_summary,
    )


async def _enrich_recommendations(
    recommendations: tuple[Recommendation, ...],
    analysis: AnalysisResult,
    *,
    client: YandexAiClient,
) -> tuple[Recommendation, ...]:
    """Отправляет запросы к AI параллельно, но с ограничением конкурентности."""
    category_map = {cat.category: cat for cat in analysis.categories}
    semaphore = asyncio.Semaphore(5)

    tasks = [
        _enrich_one(rec, category_map, client=client, semaphore=semaphore)
        for rec in recommendations
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    any_changed = False
    for original, result in zip(recommendations, results):
        if isinstance(result, Exception):
            logger.warning(
                "AI enrichment failed for recommendation %r: %s",
                original.code,
                type(result).__name__,
            )
            enriched.append(original)
        elif result is not None and result != original:
            enriched.append(result)
            any_changed = True
        else:
            enriched.append(original)

    return tuple(enriched) if any_changed else recommendations


async def _enrich_one(
    recommendation: Recommendation,
    category_map: dict[str, CategoryResult],
    *,
    client: YandexAiClient,
    semaphore: asyncio.Semaphore,
) -> Recommendation:
    """Генерирует AI-план для одной рекомендации на основе фактов анализа."""
    user_prompt = _build_user_prompt(recommendation, category_map)
    
    async with semaphore:
        ai_plan = await client.complete(_SYSTEM_PROMPT, user_prompt)

    if not ai_plan or not ai_plan.strip():
        return recommendation

    return replace(recommendation, ai_action_plan=ai_plan.strip())


def _build_user_prompt(
    recommendation: Recommendation,
    category_map: dict[str, CategoryResult],
) -> str:
    """Строит промпт только из агрегированных фактов — без путей, имён файлов, токенов."""
    lines = [
        f"Рекомендация: {recommendation.action}",
        f"Проблема: {recommendation.problem}",
        f"Причина: {recommendation.rationale}",
        "",
        "Факты из анализа:",
    ]

    # Добавляем метрики из категорий, связанных с рекомендацией.
    # Связь определяем по prefix-коду рекомендации (cicd-*, activity-*, и т.д.).
    rec_category_prefix = recommendation.code.split("-")[0] if "-" in recommendation.code else ""
    relevant_categories = [
        cat for code, cat in category_map.items()
        if rec_category_prefix and code.startswith(rec_category_prefix)
    ] or list(category_map.values())

    has_facts = False
    for category in relevant_categories:
        if category.score is not None:
            lines.append(f"- Категория «{category.category}»: оценка {category.score:.1f}/100")
        for metric in category.metrics:
            if metric.value is not None:
                lines.append(f"- Метрика «{metric.code}»: {metric.value} ({metric.summary})")
                has_facts = True

    # Добавляем evidence рекомендации (агрегированные, без путей).
    for ev in recommendation.evidence:
        lines.append(f"- {ev.source} / {ev.reference}: {ev.summary}")
        has_facts = True

    if not has_facts:
        lines.append("- Конкретных фактических данных нет.")

    lines.extend([
        "",
        "На основе этих данных напиши конкретный пошаговый план устранения (3–5 шагов).",
    ])
    return "\n".join(lines)
