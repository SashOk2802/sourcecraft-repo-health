"""Тесты AI-обогащения рекомендаций."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from backend.app.ai.enricher import enrich_execution
from backend.app.analysis.runner import AnalysisExecution
from backend.app.contracts import (
    AnalysisResult,
    CategoryResult,
    DataStatus,
    MetricResult,
    Recommendation,
    RecommendationPriority,
    RepositoryRef,
)
from backend.app.scoring.engine import ScoreSummary


def _make_repository() -> RepositoryRef:
    return RepositoryRef(
        id="repo-1",
        organization_slug="org",
        repository_slug="repo",
    )


def _make_recommendation(code: str = "cicd-fix-e2e") -> Recommendation:
    return Recommendation(
        code=code,
        priority=RecommendationPriority.P1,
        problem="Есть повторяющиеся падения.",
        action="Разобрать падения CI.",
        rationale="Нестабильный CI блокирует деплой.",
    )


def _make_category() -> CategoryResult:
    return CategoryResult(
        category="cicd",
        status=DataStatus.MEASURED,
        score=72.0,
        summary="72/100",
        metrics=(
            MetricResult(
                code="failed-runs",
                value=5,
                normalized_score=50.0,
                summary="5 неуспешных запусков",
            ),
        ),
    )


def _make_score_summary() -> ScoreSummary:
    from backend.app.scoring.engine import CategoryContribution

    return ScoreSummary(
        score=72.0,
        measured_weight=100,
        applicable_weight=100,
        coverage=1.0,
        is_preliminary=False,
        score_limit=None,
        uncapped_score=None,
        categories=(
            CategoryContribution(
                category="cicd",
                weight=20,
                effective_weight=20,
                points=14.4,
            ),
        ),
    )


def _make_execution(recommendations: tuple[Recommendation, ...] = ()) -> AnalysisExecution:
    now = datetime.now(UTC)
    analysis = AnalysisResult(
        repository=_make_repository(),
        analyzed_at=now,
        commit_sha="abc123",
        categories=(_make_category(),),
        score=72.0,
        methodology_version="v1",
        recommendations=recommendations,
    )
    return AnalysisExecution(analysis=analysis, score_summary=_make_score_summary())


class TestEnrichExecution(unittest.IsolatedAsyncioTestCase):
    async def test_no_client_returns_unchanged(self) -> None:
        """Без AI-клиента рекомендации возвращаются без изменений."""
        rec = _make_recommendation()
        execution = _make_execution((rec,))
        result = await enrich_execution(execution, client=None)
        self.assertIs(result, execution)

    async def test_empty_recommendations_returns_unchanged(self) -> None:
        """Без рекомендаций enrich ничего не делает."""
        execution = _make_execution(())
        mock_client = AsyncMock()
        result = await enrich_execution(execution, client=mock_client)
        self.assertIs(result, execution)
        mock_client.complete.assert_not_called()

    async def test_successful_enrichment_adds_ai_plan(self) -> None:
        """При успешном ответе AI план появляется в рекомендации."""
        rec = _make_recommendation()
        execution = _make_execution((rec,))
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(return_value="1. Проверь логи\n2. Исправь тест")

        result = await enrich_execution(execution, client=mock_client)

        self.assertIsNot(result, execution)
        enriched_rec = result.analysis.recommendations[0]
        self.assertEqual(enriched_rec.ai_action_plan, "1. Проверь логи\n2. Исправь тест")
        # Детерминированные поля не изменились
        self.assertEqual(enriched_rec.code, rec.code)
        self.assertEqual(enriched_rec.priority, rec.priority)
        self.assertEqual(enriched_rec.problem, rec.problem)

    async def test_client_error_leaves_recommendation_unchanged(self) -> None:
        """При ошибке AI рекомендация остаётся с оригинальным текстом."""
        rec = _make_recommendation()
        execution = _make_execution((rec,))
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(return_value=None)

        result = await enrich_execution(execution, client=mock_client)

        # Без AI-ответа — execution тот же
        self.assertIs(result, execution)
        self.assertIsNone(result.analysis.recommendations[0].ai_action_plan)

    async def test_client_exception_leaves_recommendation_unchanged(self) -> None:
        """При исключении AI рекомендация остаётся с оригинальным текстом (fail-open)."""
        rec = _make_recommendation()
        execution = _make_execution((rec,))
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(side_effect=RuntimeError("network failure"))

        result = await enrich_execution(execution, client=mock_client)

        self.assertIs(result, execution)

    async def test_multiple_recommendations_called_in_parallel(self) -> None:
        """Все рекомендации обогащаются параллельно — количество вызовов совпадает."""
        recs = (
            _make_recommendation("cicd-fix-e2e"),
            _make_recommendation("doc-missing-readme"),
        )
        execution = _make_execution(recs)
        call_count = 0

        async def fake_complete(system: str, user: str) -> str:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0)  # уступаем event loop
            return f"AI план #{call_count}"

        mock_client = AsyncMock()
        mock_client.complete = fake_complete

        result = await enrich_execution(execution, client=mock_client)

        self.assertEqual(call_count, 2)
        plans = [r.ai_action_plan for r in result.analysis.recommendations]
        self.assertTrue(all(p is not None and p.startswith("AI план") for p in plans))

    async def test_score_summary_preserved(self) -> None:
        """score_summary не меняется после обогащения."""
        rec = _make_recommendation()
        execution = _make_execution((rec,))
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(return_value="план")

        result = await enrich_execution(execution, client=mock_client)

        self.assertIs(result.score_summary, execution.score_summary)
