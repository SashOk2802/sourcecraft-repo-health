"""Связывает запуск анализаторов, снимок отчёта и жизненный цикл задания."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from backend.app.analysis.jobs import AnalysisJob, AnalysisJobStatus, AnalysisJobStore
from backend.app.analysis.runner import AnalyzerRegistration, run_analysis
from backend.app.analysis.store import AnalysisStore
from backend.app.contracts import AnalysisContext

logger = logging.getLogger(__name__)



class AnalysisExecutionService:
    """Выполняет один уже созданный запуск и сохраняет его конечное состояние."""

    def __init__(
        self,
        *,
        job_store: AnalysisJobStore,
        snapshot_store: AnalysisStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._job_store = job_store
        self._snapshot_store = snapshot_store
        self._clock = clock or _utc_now

    async def create_job(self, context: AnalysisContext, analysis_id: str) -> AnalysisJob:
        """Создаёт queued-запуск до постановки его в очередь обработчику."""

        job = AnalysisJob.queued(
            analysis_id=analysis_id,
            repository_id=context.repository.id,
            created_at=self._clock(),
        )
        return await self._job_store.create(job)

    async def execute(
        self,
        *,
        analysis_id: str,
        context: AnalysisContext,
        analyzers: Iterable[AnalyzerRegistration],
    ) -> AnalysisJob:
        """Выполняет анализаторы и сохраняет terminal-состояние вместо исключения наружу."""

        await self._job_store.mark_running(analysis_id, self._clock())
        try:
            execution = run_analysis(context, analyzers)
            await self._snapshot_store.save(analysis_id, execution)
        except Exception:
            logger.exception(
                "Не удалось выполнить запуск анализа.",
                extra={"analysis_id": analysis_id, "repository_id": context.repository.id},
            )
            return await self._job_store.finish(
                analysis_id,
                status=AnalysisJobStatus.FAILED,
                finished_at=self._clock(),
                error_code="analysis_execution_failed",
                error_summary="Не удалось выполнить анализ репозитория.",
            )

        status = (
            AnalysisJobStatus.PARTIAL
            if execution.score_summary.is_preliminary
            else AnalysisJobStatus.COMPLETED
        )
        return await self._job_store.finish(
            analysis_id,
            status=status,
            finished_at=self._clock(),
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)
