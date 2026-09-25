"""Точка входа HTTP для SourceCraft Repo Health."""

from __future__ import annotations

import hmac
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from backend.app.analysis import (
    AnalysisDispatcher,
    AnalysisExecutionService,
    AnalysisJob,
    AnalysisJobStore,
    AnalysisSnapshot,
    AnalysisStore,
    InMemoryAnalysisJobStore,
    InMemoryAnalysisStore,
    InProcessAnalysisDispatcher,
    PostgresAnalysisJobStore,
    PostgresAnalysisStore,
    normalize_analysis_id,
)
from backend.app.analysis.dispatch import AnalysisPrincipal
from backend.app.analyzers.registration import project_life_analyzer_provider
from backend.app.contracts import AnalysisContext
from backend.app.integrations.sourcecraft import SourceCraftClient
from backend.app.launch import (
    AnalysisLaunchError,
    SourceCraftRepositoryContextResolver,
    build_request_opener,
    clear_request_token,
    principal_from_authorization,
)

PrincipalProvider = Callable[[Request], Awaitable[AnalysisPrincipal]]


def create_app(
    *,
    analysis_store: AnalysisStore | None = None,
    job_store: AnalysisJobStore | None = None,
    analysis_dispatcher: AnalysisDispatcher | None = None,
    principal_provider: PrincipalProvider | None = None,
) -> FastAPI:
    """Создаёт HTTP-приложение с хранилищами и необязательным worker анализа."""

    store = analysis_store or _default_analysis_store()
    jobs = job_store or (
        InMemoryAnalysisJobStore()
        if analysis_store is not None
        else _default_analysis_job_store()
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.start()
        await jobs.start()
        if analysis_dispatcher is not None:
            await analysis_dispatcher.start()
        try:
            yield
        finally:
            if analysis_dispatcher is not None:
                await analysis_dispatcher.close()
            await jobs.close()
            await store.close()

    app = FastAPI(
        title="SourceCraft Repo Health",
        version="0.1.0",
        description="Анализ здоровья репозиториев SourceCraft.",
        lifespan=lifespan,
    )
    app.state.analysis_store = store
    app.state.analysis_job_store = jobs
    app.state.analysis_dispatcher = analysis_dispatcher
    app.state.principal_provider = principal_provider

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Возвращает состояние процесса для локальной разработки и проверок развёртывания."""

        return {"status": "ok"}

    @app.get("/api/v1/health", tags=["system"])
    async def api_health() -> dict[str, str]:
        """Возвращает endpoint с префиксом API для proxy frontend."""

        return {"status": "ok"}

    @app.post(
        "/api/v1/repositories/{repository_id}/analyses",
        tags=["analyses"],
        status_code=202,
    )
    async def request_analysis(
        repository_id: str,
        request: Request,
    ) -> dict[str, object]:
        """Создаёт queued-анализ от имени проверенного пользователя."""

        if not repository_id.strip():
            raise HTTPException(status_code=422, detail="Repository id must not be empty.")
        if analysis_dispatcher is None:
            raise HTTPException(
                status_code=503,
                detail="Analysis dispatch is not configured.",
            )
        if principal_provider is None:
            raise HTTPException(
                status_code=503,
                detail="Analysis authentication is not configured.",
            )

        try:
            principal = await principal_provider(request)
        except (PermissionError, ValueError) as error:
            raise HTTPException(
                status_code=401,
                detail="Authentication required.",
            ) from error

        try:
            try:
                job = await analysis_dispatcher.submit(repository_id, principal)
            except LookupError as error:
                raise HTTPException(status_code=404, detail="Repository not found.") from error
            except AnalysisLaunchError as error:
                raise HTTPException(
                    status_code=error.status_code,
                    detail=error.detail,
                    headers=_launch_error_headers(error),
                ) from error
            except PermissionError as error:
                raise HTTPException(status_code=403, detail="Repository access denied.") from error
            except ValueError as error:
                raise HTTPException(status_code=422, detail="Invalid analysis request.") from error
            return _analysis_job_status_payload(job, snapshot=None)
        finally:
            # Копия токена уже в фоновой задаче. На задаче запроса он больше не нужен.
            clear_request_token()

    @app.get("/api/v1/analyses/{analysis_id}", tags=["analyses"])
    async def get_analysis_status(analysis_id: str, request: Request) -> dict[str, object]:
        """Возвращает состояние анализа только его владельцу."""

        normalized_id = _normalize_analysis_id(analysis_id)
        job = await _authorized_job(request, jobs, principal_provider, normalized_id)
        snapshot = await store.get(normalized_id)
        return _analysis_job_status_payload(job, snapshot)

    @app.get("/api/v1/analyses/{analysis_id}/report", tags=["reports"])
    async def get_report(analysis_id: str, request: Request) -> dict[str, object]:
        """Возвращает JSON-отчёт только владельцу анализа."""

        normalized_id = _normalize_analysis_id(analysis_id)
        await _authorized_job(request, jobs, principal_provider, normalized_id)
        snapshot = await _require_snapshot(store, normalized_id)
        return snapshot.report

    @app.get(
        "/api/v1/analyses/{analysis_id}/report.md",
        tags=["reports"],
        response_class=PlainTextResponse,
    )
    async def get_markdown_report(analysis_id: str, request: Request) -> PlainTextResponse:
        """Возвращает Markdown-отчёт только владельцу анализа."""

        normalized_id = _normalize_analysis_id(analysis_id)
        await _authorized_job(request, jobs, principal_provider, normalized_id)
        snapshot = await _require_snapshot(store, normalized_id)
        return PlainTextResponse(snapshot.markdown, media_type="text/markdown")

    return app


def create_sourcecraft_app(
    *,
    analysis_store: AnalysisStore | None = None,
    job_store: AnalysisJobStore | None = None,
    http_client_factory: Callable[[], httpx.Client] | None = None,
    clock: Callable[[], datetime] | None = None,
    analysis_id_factory: Callable[[], str] | None = None,
) -> FastAPI:
    """Собирает рабочее приложение: диспетчер, resolver и principal из Bearer.

    `create_app()` без этих аргументов по-прежнему отвечает 503. Токен в сборку
    не передаётся: его приносит заголовок Authorization конкретного запроса.
    """

    store = analysis_store or _default_analysis_store()
    jobs = job_store or (
        InMemoryAnalysisJobStore()
        if analysis_store is not None
        else _default_analysis_job_store()
    )
    open_bound_client = build_request_opener(http_client_factory)

    def open_client(context: AnalysisContext) -> SourceCraftClient:
        # Контекст запуска токена не содержит. Клиент открывается в потоке collect.
        del context
        return open_bound_client()

    execution_service = AnalysisExecutionService(job_store=jobs, snapshot_store=store)
    context_resolver = SourceCraftRepositoryContextResolver(open_bound_client, clock=clock)
    dispatcher = InProcessAnalysisDispatcher(
        execution_service=execution_service,
        context_resolver=context_resolver,
        analyzer_provider=project_life_analyzer_provider(open_client),
        analysis_id_factory=analysis_id_factory,
    )
    return create_app(
        analysis_store=store,
        job_store=jobs,
        analysis_dispatcher=dispatcher,
        principal_provider=principal_from_authorization,
    )


def _default_analysis_store() -> AnalysisStore:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return PostgresAnalysisStore(database_url)
    return InMemoryAnalysisStore()


def _default_analysis_job_store() -> AnalysisJobStore:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return PostgresAnalysisJobStore(database_url)
    return InMemoryAnalysisJobStore()


async def _authorized_job(
    request: Request,
    jobs: AnalysisJobStore,
    principal_provider: PrincipalProvider | None,
    analysis_id: str,
) -> AnalysisJob:
    """Сверяет Bearer с несекретным subject задания. Без токена — 401, чужой — 403."""

    if principal_provider is None:
        raise HTTPException(
            status_code=503,
            detail="Analysis authentication is not configured.",
        )
    try:
        try:
            principal = await principal_provider(request)
        except (PermissionError, ValueError) as error:
            raise HTTPException(status_code=401, detail="Authentication required.") from error
    finally:
        clear_request_token()

    job = await jobs.get(analysis_id)
    if job is None or not job.owner_subject:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    if not hmac.compare_digest(
        job.owner_subject.encode("utf-8"),
        principal.subject.encode("utf-8"),
    ):
        raise HTTPException(status_code=403, detail="Analysis access denied.")
    return job


def _normalize_analysis_id(analysis_id: str) -> str:
    try:
        return normalize_analysis_id(analysis_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Analysis not found.") from error


async def _require_snapshot(store: AnalysisStore, analysis_id: str) -> AnalysisSnapshot:
    snapshot = await store.get(analysis_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return snapshot


def _analysis_status_payload(
    snapshot: AnalysisSnapshot,
    analysis_id: str,
) -> dict[str, object]:
    analysis = snapshot.report["analysis"]
    return {
        "id": analysis_id,
        "status": analysis["status"],
        "repository": snapshot.report["repository"],
        "score": snapshot.report["score"],
        "isPreliminary": analysis["isPreliminary"],
        "reportUrl": f"/api/v1/analyses/{analysis_id}/report",
        "markdownReportUrl": f"/api/v1/analyses/{analysis_id}/report.md",
    }


def _analysis_job_status_payload(
    job: AnalysisJob,
    snapshot: AnalysisSnapshot | None,
) -> dict[str, object]:
    """Строит единый ответ для queued, running, failed и готового анализа."""

    error = (
        {"code": job.error_code, "summary": job.error_summary}
        if job.error_code is not None
        else None
    )
    payload: dict[str, object] = {
        "id": job.analysis_id,
        "status": job.status.value,
        "repository": {"id": job.repository_id},
        "score": None,
        "isPreliminary": None,
        "createdAt": _format_timestamp(job.created_at),
        "startedAt": _format_timestamp(job.started_at),
        "finishedAt": _format_timestamp(job.finished_at),
        "error": error,
        "reportUrl": None,
        "markdownReportUrl": None,
    }
    if snapshot is None:
        return payload

    payload.update(_analysis_status_payload(snapshot, job.analysis_id))
    payload["status"] = job.status.value
    payload["createdAt"] = _format_timestamp(job.created_at)
    payload["startedAt"] = _format_timestamp(job.started_at)
    payload["finishedAt"] = _format_timestamp(job.finished_at)
    payload["error"] = error
    return payload


def _launch_error_headers(error: AnalysisLaunchError) -> dict[str, str] | None:
    if error.retry_after_seconds is None:
        return None
    return {"Retry-After": str(error.retry_after_seconds)}


def _format_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


app = create_sourcecraft_app()
