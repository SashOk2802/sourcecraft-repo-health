"""Постановка запусков анализа в фоновое выполнение внутри процесса."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import uuid4

from backend.app.analysis.executor import AnalysisExecutionService
from backend.app.analysis.jobs import AnalysisJob
from backend.app.analysis.runner import AnalyzerRegistration
from backend.app.contracts import AnalysisContext


@dataclass(frozen=True, slots=True)
class AnalysisPrincipal:
    """Проверенная идентичность инициатора анализа без токенов и секретов."""

    subject: str

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("principal subject must not be empty")


class RepositoryContextResolver(Protocol):
    """Проверяет доступ инициатора и строит контекст анализа репозитория."""

    async def resolve(
        self,
        repository_id: str,
        principal: AnalysisPrincipal,
    ) -> AnalysisContext:
        """Возвращает контекст только для репозитория, доступного principal."""


AnalyzerProvider = Callable[[AnalysisContext], Iterable[AnalyzerRegistration]]


class AnalysisDispatcher(Protocol):
    """Ставит запуск в фоновую обработку и завершает фоновые задачи при остановке."""

    async def start(self) -> None:
        """Регистрирует worker и восстанавливает только abandoned-задания."""

    async def submit(
        self,
        repository_id: str,
        principal: AnalysisPrincipal,
    ) -> AnalysisJob:
        """Создаёт queued-задание и планирует его выполнение для инициатора."""

    async def close(self) -> None:
        """Дожидается уже поставленных запусков перед закрытием приложения."""


class InProcessAnalysisDispatcher:
    """Минимальный worker для одного процесса FastAPI.

    Каждому экземпляру назначается случайный worker ID и heartbeat lease в
    PostgreSQL. При старте он завершает только задания с истёкшей lease другого
    worker; активные задания соседнего процесса не затрагиваются. Синхронные
    анализаторы выполняются сервисом в отдельном потоке, поэтому не блокируют
    event loop.
    """

    def __init__(
        self,
        *,
        execution_service: AnalysisExecutionService,
        context_resolver: RepositoryContextResolver,
        analyzer_provider: AnalyzerProvider,
        analysis_id_factory: Callable[[], str] | None = None,
        worker_id: str | None = None,
        heartbeat_interval: timedelta = timedelta(seconds=10),
    ) -> None:
        if heartbeat_interval <= timedelta():
            raise ValueError("heartbeat_interval must be positive")
        self._execution_service = execution_service
        self._context_resolver = context_resolver
        self._analyzer_provider = analyzer_provider
        self._analysis_id_factory = analysis_id_factory or _new_analysis_id
        self._worker_id = worker_id or _new_worker_id()
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._started = False
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        """Регистрирует lease worker и безопасно восстанавливает abandoned-задания."""

        async with self._start_lock:
            if self._started:
                return
            await self._execution_service.start_worker(self._worker_id)
            self._heartbeat_task = asyncio.create_task(
                self._send_heartbeats(),
                name=f"analysis-worker-heartbeat-{self._worker_id}",
            )
            self._started = True

    async def submit(
        self,
        repository_id: str,
        principal: AnalysisPrincipal,
    ) -> AnalysisJob:
        """Проверяет доступ, сохраняет queued-задание и запускает worker."""

        await self.start()
        context = await self._context_resolver.resolve(repository_id, principal)
        if context.repository.id != repository_id:
            raise ValueError("resolved context does not match repository_id")

        analyzers = tuple(self._analyzer_provider(context))
        job = await self._execution_service.create_job(
            context,
            self._analysis_id_factory(),
            owner_subject=principal.subject,
            worker_id=self._worker_id,
        )
        task = asyncio.create_task(
            self._execute(job.analysis_id, context, analyzers),
            name=f"analysis-{job.analysis_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def close(self) -> None:
        """Сохраняет lease до завершения задач, затем останавливает heartbeat."""

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        self._started = False

    async def _send_heartbeats(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval.total_seconds())
            await self._execution_service.heartbeat_worker(self._worker_id)
            await self._execution_service.recover_abandoned_workers()

    async def _execute(
        self,
        analysis_id: str,
        context: AnalysisContext,
        analyzers: tuple[AnalyzerRegistration, ...],
    ) -> None:
        await self._execution_service.execute(
            analysis_id=analysis_id,
            context=context,
            analyzers=analyzers,
            worker_id=self._worker_id,
        )


def _new_analysis_id() -> str:
    return f"analysis-{uuid4().hex}"


def _new_worker_id() -> str:
    return f"worker-{uuid4().hex}"
