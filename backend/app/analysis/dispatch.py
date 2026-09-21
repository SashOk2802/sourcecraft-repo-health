"""Постановка запусков анализа в фоновое выполнение внутри процесса."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
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

    Синхронные анализаторы выполняются сервисом в отдельном потоке, поэтому не
    блокируют event loop. Интеграция SourceCraft реализует
    RepositoryContextResolver с проверкой principal, а авторы категорий передают
    AnalyzerProvider. При появлении внешнего worker этот класс заменяется без
    изменения endpoint.
    """

    def __init__(
        self,
        *,
        execution_service: AnalysisExecutionService,
        context_resolver: RepositoryContextResolver,
        analyzer_provider: AnalyzerProvider,
        analysis_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._execution_service = execution_service
        self._context_resolver = context_resolver
        self._analyzer_provider = analyzer_provider
        self._analysis_id_factory = analysis_id_factory or _new_analysis_id
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(
        self,
        repository_id: str,
        principal: AnalysisPrincipal,
    ) -> AnalysisJob:
        """Проверяет доступ, сохраняет queued-задание и запускает worker."""

        context = await self._context_resolver.resolve(repository_id, principal)
        if context.repository.id != repository_id:
            raise ValueError("resolved context does not match repository_id")

        analyzers = tuple(self._analyzer_provider(context))
        job = await self._execution_service.create_job(context, self._analysis_id_factory())
        task = asyncio.create_task(
            self._execute(job.analysis_id, context, analyzers),
            name=f"analysis-{job.analysis_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def close(self) -> None:
        """Не закрывает хранилища, пока не завершатся уже поставленные задания."""

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

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
        )


def _new_analysis_id() -> str:
    return f"analysis-{uuid4().hex}"
