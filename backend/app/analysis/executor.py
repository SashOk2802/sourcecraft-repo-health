"""Связывает запуск анализаторов, снимок отчёта и жизненный цикл задания."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from backend.app.ai.client import YandexAiClient
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
        worker_lease_timeout: timedelta = timedelta(seconds=30),
        ai_client: YandexAiClient | None = None,
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
        if worker_lease_timeout <= timedelta():
            raise ValueError("worker_lease_timeout must be positive")
        self._clock = clock or _utc_now
        self._worker_lease_timeout = worker_lease_timeout
        self._ai_client = ai_client

    async def start_worker(self, worker_id: str) -> tuple[AnalysisJob, ...]:
        """Продлевает собственную lease и завершает только задания мёртвых worker."""

        now = self._clock()
        await self._job_store.heartbeat_worker(worker_id, now)
        return await self._job_store.recover_abandoned(
            finished_at=now,
            stale_before=now - self._worker_lease_timeout,
        )

    async def heartbeat_worker(self, worker_id: str) -> None:
        """Продлевает lease worker во время длительных запусков."""

        await self._job_store.heartbeat_worker(worker_id, self._clock())

    async def recover_abandoned_workers(self) -> tuple[AnalysisJob, ...]:
        """Завершает задания владельцев, не продливших lease за допустимое время."""

        now = self._clock()
        return await self._job_store.recover_abandoned(
            finished_at=now,
            stale_before=now - self._worker_lease_timeout,
        )

    async def create_job(
        self,
        context: AnalysisContext,
        analysis_id: str,
        *,
        owner_subject: str,
        worker_id: str | None = None,
    ) -> AnalysisJob:
        """Создаёт queued-запуск до постановки его в очередь обработчику."""

        job = AnalysisJob.queued(
            analysis_id=analysis_id,
            repository_id=context.repository.id,
            created_at=self._clock(),
            owner_subject=owner_subject,
            worker_id=worker_id,
        )
        return await self._job_store.create(job)

    async def create_or_get_job(
        self,
        context: AnalysisContext,
        analysis_id: str,
        *,
        owner_subject: str,
        worker_id: str | None = None,
    ) -> tuple[AnalysisJob, bool]:
        """Создаёт job либо безопасно возвращает уже созданный с тем же устойчивым ID."""

        try:
            return (
                await self.create_job(
                    context,
                    analysis_id,
                    owner_subject=owner_subject,
                    worker_id=worker_id,
                ),
                True,
            )
        except ValueError:
            existing = await self._job_store.get(analysis_id)
            if (
                existing is None
                or existing.repository_id != context.repository.id
                or existing.owner_subject != owner_subject
            ):
                raise
            return existing, False

    async def execute(
        self,
        *,
        analysis_id: str,
        context: AnalysisContext,
        analyzers: Iterable[AnalyzerRegistration],
        worker_id: str | None = None,
    ) -> AnalysisJob:
        """Выполняет анализаторы и сохраняет terminal-состояние вместо исключения наружу."""

        job = await self._job_store.mark_running(
            analysis_id,
            self._clock(),
            worker_id=worker_id,
        )
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
                worker_id=worker_id,
            )

        try:
            execution = await asyncio.to_thread(
                run_analysis,
                context,
                tuple(analyzers),
            )
            from backend.app.ai.enricher import enrich_execution
            execution = await enrich_execution(execution, client=self._ai_client)
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
                    worker_id=worker_id,
                )

            await self._snapshot_store.save(job.analysis_id, execution)
            return await self._job_store.finish(
                job.analysis_id,
                status=status,
                finished_at=self._clock(),
                worker_id=worker_id,
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
                worker_id=worker_id,
            )


def _utc_now() -> datetime:
    return datetime.now(UTC)
