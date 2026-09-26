"""Точка входа HTTP для SourceCraft Repo Health."""

from __future__ import annotations

import hmac
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

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
from backend.app.analysis.providers import sourcecraft_analyzer_provider
from backend.app.analyzers.registration import HistoryReader, project_life_analyzer_provider
from backend.app.contracts import AnalysisContext
from backend.app.identity import (
    YandexAuthenticationError,
    YandexAuthService,
    YandexProviderError,
    create_yandex_auth_service_from_environment,
)
from backend.app.integrations.sourcecraft import SourceCraftClient
from backend.app.integrations.sourcecraft_repository import (
    SourceCraftRepositoryUnavailableError,
    create_sourcecraft_public_repository_resolver_from_environment,
)
from backend.app.launch import (
    AnalysisLaunchError,
    SourceCraftRepositoryContextResolver,
    bind_request_token,
    build_request_opener,
    clear_request_token,
)
from backend.app.scoring.methodology import build_methodology_payload

PrincipalProvider = Callable[[Request], Awaitable[AnalysisPrincipal]]


def create_app(
    *,
    analysis_store: AnalysisStore | None = None,
    job_store: AnalysisJobStore | None = None,
    analysis_dispatcher: AnalysisDispatcher | None = None,
    principal_provider: PrincipalProvider | None = None,
    yandex_auth_service: YandexAuthService | None = None,
    bind_sourcecraft_token: bool = False,
) -> FastAPI:
    """Создаёт HTTP-приложение с хранилищами и необязательным worker анализа."""

    store = analysis_store or _default_analysis_store()
    jobs = job_store or (
        InMemoryAnalysisJobStore() if analysis_store is not None else _default_analysis_job_store()
    )

    effective_principal_provider = principal_provider or _yandex_principal_provider(
        yandex_auth_service,
    )
    effective_analysis_dispatcher = analysis_dispatcher
    if effective_analysis_dispatcher is None and analysis_store is None and job_store is None:
        effective_analysis_dispatcher = _create_default_analysis_dispatcher(store, jobs)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        store_started = False
        jobs_started = False
        auth_started = False
        dispatcher_started = False
        try:
            await store.start()
            store_started = True
            await jobs.start()
            jobs_started = True
            if yandex_auth_service is not None:
                await yandex_auth_service.start()
                auth_started = True
            if effective_analysis_dispatcher is not None:
                await effective_analysis_dispatcher.start()
                dispatcher_started = True
            yield
        finally:
            if dispatcher_started:
                await effective_analysis_dispatcher.close()
            if auth_started:
                await yandex_auth_service.close()
            if jobs_started:
                await jobs.close()
            if store_started:
                await store.close()

    app = FastAPI(
        title="SourceCraft Repo Health",
        version="0.1.0",
        description="Анализ здоровья репозиториев SourceCraft.",
        lifespan=lifespan,
    )
    app.state.analysis_store = store
    app.state.analysis_job_store = jobs
    app.state.analysis_dispatcher = effective_analysis_dispatcher
    app.state.principal_provider = effective_principal_provider
    app.state.yandex_auth_service = yandex_auth_service
    app.state.binds_caller_sourcecraft_token = bind_sourcecraft_token

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Возвращает состояние процесса для локальной разработки и проверок развёртывания."""

        return {"status": "ok"}

    @app.get("/api/v1/health", tags=["system"])
    async def api_health() -> dict[str, str]:
        """Возвращает endpoint с префиксом API для proxy frontend."""

        return {"status": "ok"}

    @app.get("/api/v1/methodology", tags=["methodology"])
    async def get_methodology() -> dict[str, object]:
        """Возвращает публичное описание правил текущей версии Score."""

        return build_methodology_payload()

    @app.get("/api/v1/auth/yandex/start", tags=["authentication"])
    async def start_yandex_login() -> RedirectResponse:
        """Начинает Authorization Code + PKCE поток Яндекс ID."""

        auth = _require_yandex_auth(yandex_auth_service)
        return RedirectResponse(await auth.begin(), status_code=307)

    @app.get("/api/v1/auth/yandex/callback", tags=["authentication"])
    async def finish_yandex_login(
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> Response:
        """Завершает вход и выдаёт браузеру непрозрачную серверную сессию."""

        auth = _require_yandex_auth(yandex_auth_service)
        if error is not None or code is None or state is None:
            await auth.cancel(state)
            raise HTTPException(status_code=401, detail="Yandex authentication was not completed.")

        try:
            session_token, _ = await auth.complete(code, state)
        except YandexAuthenticationError as auth_error:
            raise HTTPException(
                status_code=401,
                detail="Yandex authentication was not completed.",
            ) from auth_error
        except YandexProviderError as provider_error:
            raise HTTPException(
                status_code=502,
                detail="Yandex authentication provider is unavailable.",
            ) from provider_error

        response = RedirectResponse(auth.settings.success_redirect_path, status_code=303)
        response.set_cookie(
            key=auth.settings.cookie_name,
            value=session_token,
            max_age=int(auth.settings.session_ttl.total_seconds()),
            httponly=True,
            secure=auth.settings.cookie_secure,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/api/v1/me", tags=["authentication"])
    async def get_current_user(request: Request) -> dict[str, str]:
        """Возвращает минимальный профиль текущей серверной сессии."""

        auth = _require_yandex_auth(yandex_auth_service)
        user = await _require_yandex_user(auth, request)
        return {"id": user.id, "login": user.login}

    @app.post("/api/v1/auth/logout", tags=["authentication"], status_code=204)
    async def logout(request: Request) -> Response:
        """Отзывает сессию на сервере и удаляет cookie у браузера."""

        auth = _require_yandex_auth(yandex_auth_service)
        await auth.logout(request.cookies.get(auth.settings.cookie_name))
        response = Response(status_code=204)
        response.delete_cookie(
            key=auth.settings.cookie_name,
            httponly=True,
            secure=auth.settings.cookie_secure,
            samesite="lax",
            path="/",
        )
        return response

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
        if effective_analysis_dispatcher is None:
            raise HTTPException(
                status_code=503,
                detail="Analysis dispatch is not configured.",
            )
        if effective_principal_provider is None:
            raise HTTPException(
                status_code=503,
                detail="Analysis authentication is not configured.",
            )

        try:
            if bind_sourcecraft_token:
                try:
                    bind_request_token(request)
                except PermissionError as error:
                    raise HTTPException(
                        status_code=401,
                        detail="SourceCraft token is required.",
                    ) from error
            try:
                principal = await effective_principal_provider(request)
            except (PermissionError, ValueError) as error:
                raise HTTPException(
                    status_code=401,
                    detail="Authentication required.",
                ) from error
            try:
                job = await effective_analysis_dispatcher.submit(repository_id, principal)
            except SourceCraftRepositoryUnavailableError as error:
                raise HTTPException(
                    status_code=503,
                    detail="SourceCraft repository catalog is unavailable.",
                ) from error
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
            clear_request_token()

    @app.get("/api/v1/analyses/{analysis_id}", tags=["analyses"])
    async def get_analysis_status(analysis_id: str, request: Request) -> dict[str, object]:
        """Возвращает состояние анализа только его владельцу."""

        normalized_id = _normalize_analysis_id(analysis_id)
        job = await _authorized_job(request, jobs, effective_principal_provider, normalized_id)
        snapshot = await store.get(normalized_id)
        return _analysis_job_status_payload(job, snapshot)

    @app.get("/api/v1/analyses/{analysis_id}/report", tags=["reports"])
    async def get_report(analysis_id: str, request: Request) -> dict[str, object]:
        """Возвращает JSON-отчёт только владельцу анализа."""

        normalized_id = _normalize_analysis_id(analysis_id)
        await _authorized_job(request, jobs, effective_principal_provider, normalized_id)
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
        await _authorized_job(request, jobs, effective_principal_provider, normalized_id)
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
    principal_provider: PrincipalProvider | None = None,
    yandex_auth_service: YandexAuthService | None = None,
    read_commit_history: HistoryReader | None = None,
) -> FastAPI:
    """Явный пользовательский запуск Activity и Issues токеном вызывающего.

    Это не вход процесса. Рабочее приложение — `create_app()`: оно берёт
    public-каталог и все шесть категорий, а сервисный токен не открывает
    private/internal. Bearer здесь не копируется в subject.
    """

    store = analysis_store or _default_analysis_store()
    jobs = job_store or (
        InMemoryAnalysisJobStore()
        if analysis_store is not None
        else _default_analysis_job_store()
    )
    open_bound_client = build_request_opener(http_client_factory)

    def open_client(context: AnalysisContext) -> SourceCraftClient:
        del context
        return open_bound_client()

    execution_service = AnalysisExecutionService(job_store=jobs, snapshot_store=store)
    context_resolver = SourceCraftRepositoryContextResolver(open_bound_client, clock=clock)
    dispatcher = InProcessAnalysisDispatcher(
        execution_service=execution_service,
        context_resolver=context_resolver,
        analyzer_provider=project_life_analyzer_provider(open_client, read_commit_history),
        analysis_id_factory=analysis_id_factory,
    )
    return create_app(
        analysis_store=store,
        job_store=jobs,
        analysis_dispatcher=dispatcher,
        principal_provider=principal_provider,
        yandex_auth_service=yandex_auth_service,
        bind_sourcecraft_token=True,
    )


def _require_yandex_auth(auth: YandexAuthService | None) -> YandexAuthService:
    if auth is None:
        raise HTTPException(status_code=503, detail="Yandex authentication is not configured.")
    return auth


async def _require_yandex_user(
    auth: YandexAuthService,
    request: Request,
):
    try:
        return await auth.require_user(request.cookies.get(auth.settings.cookie_name))
    except PermissionError as error:
        raise HTTPException(status_code=401, detail="Authentication required.") from error


def _yandex_principal_provider(
    auth: YandexAuthService | None,
) -> PrincipalProvider | None:
    if auth is None:
        return None

    async def provide(request: Request) -> AnalysisPrincipal:
        user = await auth.require_user(request.cookies.get(auth.settings.cookie_name))
        return AnalysisPrincipal(user.id)

    return provide


def _create_default_analysis_dispatcher(
    store: AnalysisStore,
    jobs: AnalysisJobStore,
) -> AnalysisDispatcher | None:
    """Создаёт production worker только для явно настроенного public-каталога.

    Личные SourceCraft-подключения собирает `create_sourcecraft_app()`. Пока
    каталог не сконфигурирован, endpoint остаётся fail-closed с 503; сервисный
    токен не используется для private/internal репозиториев произвольного пользователя.
    """

    resolver = create_sourcecraft_public_repository_resolver_from_environment()
    if resolver is None:
        return None
    return InProcessAnalysisDispatcher(
        execution_service=AnalysisExecutionService(
            job_store=jobs,
            snapshot_store=store,
        ),
        context_resolver=resolver,
        analyzer_provider=sourcecraft_analyzer_provider,
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
    """Сверяет subject сессии с владельцем задания.

    Нет сессии — 401. Нет задания, пустой владелец и чужой subject отвечают
    одним 404, чтобы id анализа не подтверждался.
    """

    if principal_provider is None:
        raise HTTPException(
            status_code=503,
            detail="Analysis authentication is not configured.",
        )
    try:
        principal = await principal_provider(request)
    except (PermissionError, ValueError) as error:
        raise HTTPException(status_code=401, detail="Authentication required.") from error

    job = await jobs.get(analysis_id)
    if job is None or not job.owner_subject:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    if not hmac.compare_digest(
        job.owner_subject.encode("utf-8"),
        principal.subject.encode("utf-8"),
    ):
        raise HTTPException(status_code=404, detail="Analysis not found.")
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


app = create_app(
    yandex_auth_service=create_yandex_auth_service_from_environment(
        os.getenv("DATABASE_URL"),
    ),
)
