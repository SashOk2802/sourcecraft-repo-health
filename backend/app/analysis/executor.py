"""Связывает запуск анализаторов, снимок отчёта и жизненный цикл задания."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from backend.app.analysis.jobs import (
    AnalysisJob,
    AnalysisJobStatus,
    AnalysisJobStore,
    PostgresAnalysisJobStore,
)
from backend.app.analysis.runner import AnalyzerRegistration, run_analysis
from backend.app.analysis.store import AnalysisStore, PostgresAnalysisStore
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
        job_store_is_postgres = isinstance(job_store, PostgresAnalysisJobStore)
        snapshot_store_is_postgres = isinstance(snapshot_store, PostgresAnalysisStore)
        if job_store_is_postgres != snapshot_store_is_postgres:
            raise ValueError(
                "PostgreSQL job and snapshot stores must be configured together."
            )
        if (
            job_store_is_postgres
            and snapshot_store_is_postgres
            and job_store.database_url != snapshot_store.database_url
        ):
            raise ValueError("Job and snapshot stores must use the same PostgreSQL database.")

        self._job_store = job_store
        self._snapshot_store = snapshot_store
        self._postgres_snapshot_store = (
            snapshot_store if snapshot_store_is_postgres else None
        )
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

        job = await self._job_store.mark_running(analysis_id, self._clock())
        if job.repository_id != context.repository.id:
            logger.error(
                "Контекст запуска не соответствует репозиторию задания.",
                extra={
                    "analysis_id": job.analysis_id,
                    "job_repository_id": job.repository_id,
                    "context_repository_id": context.repository.id,
                },
            )
            return await self._job_store.finish(
                job.analysis_id,
                status=AnalysisJobStatus.FAILED,
                finished_at=self._clock(),
                error_code="repository_mismatch",
                error_summary="Контекст запуска относится к другому репозиторию.",
            )

        try:
            execution = await asyncio.to_thread(
                run_analysis,
                context,
                tuple(analyzers),
            )
            status = (
                AnalysisJobStatus.PARTIAL
                if execution.score_summary.is_preliminary
                else AnalysisJobStatus.COMPLETED
            )
            if self._postgres_snapshot_store is not None:
                return await self._postgres_snapshot_store.save_and_finish(
                    job.analysis_id,
                    execution,
                    status=status,
                    finished_at=self._clock(),
                )

            await self._snapshot_store.save(job.analysis_id, execution)
            return await self._job_store.finish(
                job.analysis_id,
                status=status,
                finished_at=self._clock(),
            )
        except Exception:
            logger.exception(
                "Не удалось выполнить запуск анализа.",
                extra={"analysis_id": job.analysis_id, "repository_id": context.repository.id},
            )
            return await self._job_store.finish(
                job.analysis_id,
                status=AnalysisJobStatus.FAILED,
                finished_at=self._clock(),
                error_code="analysis_execution_failed",
                error_summary="Не удалось выполнить анализ репозитория.",
            )


def _utc_now() -> datetime:
    return datetime.now(UTC)
