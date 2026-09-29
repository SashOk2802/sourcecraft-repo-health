"""Точка входа HTTP для SourceCraft Repo Health."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from math import floor

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel

from backend.app.ai.client import create_client_from_environment as _create_ai_client
from backend.app.analysis import (
    AnalysisDispatcher,
    AnalysisExecutionService,
    AnalysisJob,
    AnalysisJobStatus,
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
from backend.app.analysis.personal_sourcecraft import (
    PersonalOrPublicAnalysisPlanner,
    SourceCraftConnectionRequiredError,
)
from backend.app.analysis.providers import sourcecraft_analyzer_provider
from backend.app.analyzers.registration import project_life_analyzer_provider
from backend.app.contracts import AnalysisContext
from backend.app.identity import (
    SourceCraftConnectionRejectedError,
    SourceCraftConnectionService,
    SourceCraftConnectionStatus,
    SourceCraftConnectionUnavailableError,
    YandexAuthenticationError,
    YandexAuthService,
    YandexProviderError,
    create_sourcecraft_connection_service_from_environment,
    create_yandex_auth_service_from_environment,
)
from backend.app.integrations.sourcecraft import SourceCraftClient
from backend.app.integrations.sourcecraft_public_catalog import (
    PublicRepositoryCatalog,
    create_sourcecraft_public_repository_catalog_from_environment,
)
from backend.app.integrations.sourcecraft_repositories import SourceCraftRepository
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
from backend.app.leaderboard import (
    LeaderboardFilters,
    LeaderboardPage,
    LeaderboardPageRow,
    LeaderboardService,
    LeaderboardSnapshotProjection,
    LeaderboardSort,
)
from backend.app.leaderboard.sourcecraft_catalog import SourceCraftLeaderboardRepositoryCatalog
from backend.app.reporting import render_score_badge
from backend.app.scheduling.runner import (
    SYSTEM_SCHEDULER_SUBJECT,
    PostgresAnalysisScheduleStore,
    PublicAnalysisScheduler,
)
from backend.app.scoring.methodology import build_methodology_payload

PrincipalProvider = Callable[[Request], Awaitable[AnalysisPrincipal]]
_SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_API_PATH_PREFIX = "/api/"

_COMMON_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Cross-Origin-Resource-Policy": "same-origin",
}
_API_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_API_DOCUMENT_PATHS = frozenset({"/health", "/openapi.json"})
_SENSITIVE_RESPONSE_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/me",
    "/api/v1/connections/",
    "/api/v1/repositories/",
    "/api/v1/analyses/",
)


class SourceCraftConnectionRequest(BaseModel):
    """PAT принимается только телом POST и никогда не возвращается клиенту."""

    token: str


def create_app(
    *,
    analysis_store: AnalysisStore | None = None,
    job_store: AnalysisJobStore | None = None,
    analysis_dispatcher: AnalysisDispatcher | None = None,
    principal_provider: PrincipalProvider | None = None,
    yandex_auth_service: YandexAuthService | None = None,
    sourcecraft_connection_service: SourceCraftConnectionService | None = None,
    bind_sourcecraft_token: bool = False,
    repository_catalog: PublicRepositoryCatalog | None = None,
    configure_public_repository_catalog: bool = True,
    leaderboard_service: LeaderboardService | None = None,
    analysis_scheduler: PublicAnalysisScheduler | None = None,
) -> FastAPI:
    """Создаёт HTTP-приложение с хранилищами и необязательным worker анализа."""

    store = analysis_store or _default_analysis_store()
    jobs = job_store or (
        InMemoryAnalysisJobStore() if analysis_store is not None else _default_analysis_job_store()
    )

    ondemand_locks: dict[str, asyncio.Lock] = {}
    ondemand_locks_guard = asyncio.Lock()

    async def get_ondemand_lock(repository_id: str) -> asyncio.Lock:
        async with ondemand_locks_guard:
            lock = ondemand_locks.get(repository_id)
            if lock is None:
                lock = asyncio.Lock()
                ondemand_locks[repository_id] = lock
            return lock

    effective_principal_provider = principal_provider or _yandex_principal_provider(
        yandex_auth_service,
    )
    effective_analysis_dispatcher = analysis_dispatcher
    if effective_analysis_dispatcher is None and analysis_store is None and job_store is None:
        effective_analysis_dispatcher = _create_default_analysis_dispatcher(
            store,
            jobs,
            sourcecraft_connection_service,
        )
    effective_repository_catalog = repository_catalog
    if effective_repository_catalog is None and configure_public_repository_catalog:
        effective_repository_catalog = _create_default_public_repository_catalog()
    effective_leaderboard_service = leaderboard_service
    if effective_leaderboard_service is None and effective_repository_catalog is not None:
        effective_leaderboard_service = LeaderboardService(
            analysis_store=store,
            repository_catalog=SourceCraftLeaderboardRepositoryCatalog(
                effective_repository_catalog
            ),
        )
    effective_analysis_scheduler = analysis_scheduler
    if effective_analysis_scheduler is None and configure_public_repository_catalog:
        effective_analysis_scheduler = _create_default_public_analysis_scheduler(
            dispatcher=effective_analysis_dispatcher,
            job_store=jobs,
            repository_catalog=effective_repository_catalog,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        store_started = False
        jobs_started = False
        auth_started = False
        sourcecraft_connection_started = False
        dispatcher_started = False
        scheduler_started = False
        try:
            await store.start()
            store_started = True
            await jobs.start()
            jobs_started = True
            if yandex_auth_service is not None:
                await yandex_auth_service.start()
                auth_started = True
            if sourcecraft_connection_service is not None:
                await sourcecraft_connection_service.start()
                sourcecraft_connection_started = True
            if effective_analysis_dispatcher is not None:
                await effective_analysis_dispatcher.start()
                dispatcher_started = True
            if effective_analysis_scheduler is not None:
                await effective_analysis_scheduler.start()
                scheduler_started = True
            yield
        finally:
            if scheduler_started:
                await effective_analysis_scheduler.close()
            if dispatcher_started:
                await effective_analysis_dispatcher.close()
            if sourcecraft_connection_started:
                await sourcecraft_connection_service.close()
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
    app.state.sourcecraft_connection_service = sourcecraft_connection_service
    app.state.binds_caller_sourcecraft_token = bind_sourcecraft_token
    app.state.public_repository_catalog = effective_repository_catalog
    app.state.leaderboard_service = effective_leaderboard_service
    app.state.analysis_scheduler = effective_analysis_scheduler

    @app.exception_handler(Exception)
    async def unexpected_server_error(request: Request, _: Exception) -> Response:
        """Не оставляет непойманный 500-ответ без browser-защиты."""

        response = PlainTextResponse("Internal Server Error", status_code=500)
        _apply_http_security_headers(response, request.url.path)
        return response

    @app.exception_handler(RequestValidationError)
    async def safe_request_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> Response:
        """Не отражает PAT из Pydantic ``input`` при ошибке тела подключения."""

        if request.method == "POST" and request.url.path == "/api/v1/connections/sourcecraft":
            return JSONResponse(
                status_code=422,
                content={"detail": "SourceCraft token has an invalid format."},
            )
        return await request_validation_exception_handler(request, error)

    @app.middleware("http")
    async def apply_http_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Добавляет browser-защиту, не включая межсайтовый доступ к API."""

        response = await call_next(request)
        _apply_http_security_headers(response, request.url.path)
        return response

    @app.middleware("http")
    async def reject_cross_site_mutations(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Не даёт чужому сайту менять данные с отправленной браузером cookie."""
        if _requires_csrf_protection(request, yandex_auth_service) and not _is_trusted_browser_mutation(
            request,
            yandex_auth_service.settings.callback_origin,
        ):
            response = JSONResponse(
                status_code=403,
                content={"detail": "Cross-site request rejected."},
            )
            _apply_http_security_headers(response, request.url.path)
            return response
        return await call_next(request)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Возвращает состояние процесса для локальной разработки и проверок развёртывания."""

        return {"status": "ok"}

    @app.get("/api/v1/health", tags=["system"])
    async def api_health() -> dict[str, str]:
        """Возвращает endpoint с префиксом API для proxy frontend."""

        return {"status": "ok"}

    @app.get("/api/v1/leaderboard", tags=["leaderboard"])
    async def get_leaderboard(
        language: str | None = None,
        search: str | None = None,
        sort: LeaderboardSort = LeaderboardSort.SCORE,
        include_preliminary: bool = Query(default=False, alias="includePreliminary"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=15, ge=1, le=100, alias="pageSize"),
    ) -> dict[str, object]:
        """Возвращает публичный рейтинг одной текущей версии методики."""

        if effective_leaderboard_service is None:
            raise HTTPException(status_code=503, detail="Leaderboard is not configured.")
        try:
            result = await effective_leaderboard_service.get_page(
                filters=LeaderboardFilters(language=language, search=search),
                sort=sort,
                page=page,
                page_size=page_size,
            )
        except SourceCraftRepositoryUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail="SourceCraft repository catalog is unavailable.",
            ) from error
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=503,
                detail="Leaderboard data is unavailable.",
            ) from error

        return _leaderboard_page_payload(result, include_preliminary=include_preliminary)

    @app.get(
        "/api/v1/public/repositories/{organization_slug}/{repository_slug}/health",
        tags=["public api"],
    )
    async def get_public_repository_health(
        organization_slug: str,
        repository_slug: str,
    ) -> dict[str, object]:
        """Возвращает краткую public-проекцию текущей оценки репозитория.

        Ответ не требует сессии, но формируется только из записи, которую
        SourceCraft-каталог прямо сейчас подтверждает как public. В отличие от
        owner-only отчёта здесь нет id анализа, рекомендаций, фактов или данных
        личного подключения SourceCraft.
        """

        if effective_leaderboard_service is None:
            raise HTTPException(status_code=503, detail="Public API is not configured.")
        try:
            projection = await effective_leaderboard_service.get_public_repository_snapshot(
                organization_slug,
                repository_slug,
            )
        except SourceCraftRepositoryUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail="SourceCraft repository catalog is unavailable.",
            ) from error
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=503, detail="Public API data is unavailable.") from error
        # Если снимок найден, возвращаем его
        if projection is not None:
            return _public_repository_health_payload(projection)
            
        # Если снимка нет, проверяем, существует ли репозиторий в публичном каталоге.
        # Если да, мы можем автоматически запустить первичный анализ.
        try:
            metadata = await effective_leaderboard_service.get_public_repository_metadata(
                organization_slug,
                repository_slug,
            )
        except SourceCraftRepositoryUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail="SourceCraft repository catalog is unavailable.",
            ) from error
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=503, detail="Public API data is unavailable.") from error
            
        # Публичного репозитория нет в настроенном каталоге (например, другая организация):
        # проверяем его в SourceCraft и добавляем. Дальше его подхватят рейтинг и планировщик.
        discover = getattr(effective_repository_catalog, "discover", None)
        if (
            metadata is None
            and discover is not None
            and await discover(organization_slug, repository_slug) is not None
        ):
            try:
                metadata = await effective_leaderboard_service.get_public_repository_metadata(
                    organization_slug,
                    repository_slug,
                )
            except (SourceCraftRepositoryUnavailableError, TypeError, ValueError):
                metadata = None

        if metadata is not None and effective_analysis_dispatcher is not None:
            try:
                if effective_analysis_scheduler is not None:
                    await effective_analysis_scheduler.submit_on_demand(metadata.repository_id)
                else:
                    principal = AnalysisPrincipal(subject="public-api-ondemand")
                    repo_lock = await get_ondemand_lock(metadata.repository_id)
                    async with repo_lock:
                        ondemand_history, scheduled_history = await asyncio.gather(
                            jobs.list_history_for_owner_repositories(
                                principal.subject,
                                [metadata.repository_id],
                            ),
                            jobs.list_history_for_owner_repositories(
                                SYSTEM_SCHEDULER_SUBJECT,
                                [metadata.repository_id],
                            ),
                        )
                        history = (*ondemand_history, *scheduled_history)
                        has_active = any(
                            job.status in (AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING)
                            for job in history
                        )
                        latest_terminal = max(
                            (
                                job
                                for job in history
                                if job.status not in (AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING)
                            ),
                            key=lambda j: j.created_at,
                            default=None,
                        )

                        terminal_time = None
                        if latest_terminal:
                            terminal_time = latest_terminal.finished_at or latest_terminal.created_at

                        cooldown_passed = True
                        if terminal_time:
                            cooldown_passed = (datetime.now(UTC) - terminal_time) > timedelta(hours=1)

                        if not has_active and cooldown_passed:
                            suffix = str(int(terminal_time.timestamp())) if terminal_time else "init"
                            repo_hash = hashlib.sha256(metadata.repository_id.encode("utf-8")).hexdigest()[:24]
                            await effective_analysis_dispatcher.submit(
                                repository_id=metadata.repository_id,
                                principal=principal,
                                analysis_id=f"ondemand-{repo_hash}-{suffix}",
                            )
            except SourceCraftRepositoryUnavailableError as error:
                raise HTTPException(
                    status_code=503,
                    detail="SourceCraft repository catalog is unavailable.",
                ) from error
            except SourceCraftConnectionRequiredError:
                # Публичный endpoint не раскрывает, доступен ли сейчас
                # on-demand анализ для конкретного репозитория.
                pass

        # Не различаем отсутствующий, private/internal и ещё не проанализированный
        # репозиторий: endpoint не должен становиться oracle доступа.
        raise HTTPException(status_code=404, detail="Public health score not found.")

    @app.get(
        "/api/v1/public/repositories/{organization_slug}/{repository_slug}/history",
        tags=["public api"],
    )
    async def get_public_repository_history(
        organization_slug: str,
        repository_slug: str,
    ) -> dict[str, object]:
        """Возвращает историю Score публичного репозитория по сохранённым снимкам.

        Публичность проверяется по каталогу SourceCraft при каждом запросе. Отдаются
        только дата, Score, покрытие, статус и версия методики — не больше 20 точек,
        старые первыми. SourceCraft заново не опрашивается и анализ не запускается:
        точки — это уже сохранённые снимки analysis_snapshots.
        """

        if effective_leaderboard_service is None:
            raise HTTPException(status_code=503, detail="Public API is not configured.")
        try:
            history = await effective_leaderboard_service.get_public_repository_history(
                organization_slug,
                repository_slug,
            )
        except SourceCraftRepositoryUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail="SourceCraft repository catalog is unavailable.",
            ) from error
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=503, detail="Public API data is unavailable.") from error

        # Как у /health: отсутствующий и private/internal репозиторий не различаются.
        if history is None:
            raise HTTPException(status_code=404, detail="Public health history not found.")
        return {"points": [_public_history_point_payload(projection) for projection in history]}

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

    @app.get("/api/v1/connections/sourcecraft", tags=["connections"])
    async def get_sourcecraft_connection(request: Request) -> dict[str, object]:
        """Возвращает только безопасное состояние личного SourceCraft-подключения."""

        auth = _require_yandex_auth(yandex_auth_service)
        user = await _require_yandex_user(auth, request)
        connection_service = _require_sourcecraft_connection_service(
            sourcecraft_connection_service,
        )
        return _sourcecraft_connection_payload(await connection_service.status(user.id))

    @app.post("/api/v1/connections/sourcecraft", tags=["connections"])
    async def connect_sourcecraft(
        connection: SourceCraftConnectionRequest,
        request: Request,
    ) -> dict[str, object]:
        """Проверяет PAT у SourceCraft и сохраняет только зашифрованный контейнер."""

        auth = _require_yandex_auth(yandex_auth_service)
        user = await _require_yandex_user(auth, request)
        connection_service = _require_sourcecraft_connection_service(
            sourcecraft_connection_service,
        )
        try:
            status = await connection_service.connect(user.id, connection.token)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="SourceCraft token has an invalid format.") from error
        except SourceCraftConnectionRejectedError as error:
            raise HTTPException(status_code=401, detail="SourceCraft rejected the token.") from error
        except SourceCraftConnectionUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail="SourceCraft token could not be verified right now.",
            ) from error
        return _sourcecraft_connection_payload(status)

    @app.delete("/api/v1/connections/sourcecraft", tags=["connections"], status_code=204)
    async def disconnect_sourcecraft(request: Request) -> Response:
        """Удаляет ciphertext персонального SourceCraft-подключения."""

        auth = _require_yandex_auth(yandex_auth_service)
        user = await _require_yandex_user(auth, request)
        connection_service = _require_sourcecraft_connection_service(
            sourcecraft_connection_service,
        )
        await connection_service.disconnect(user.id)
        return Response(status_code=204)

    @app.get("/api/v1/me/repositories", tags=["repositories"])
    async def get_my_repositories(request: Request) -> dict[str, object]:
        """Возвращает личный каталог либо разрешённый public fallback."""

        auth = _require_yandex_auth(yandex_auth_service)
        user = await _require_yandex_user(auth, request)

        repositories = None
        if sourcecraft_connection_service is not None:
            try:
                repositories = await sourcecraft_connection_service.list_repositories(user.id)
            except SourceCraftConnectionRejectedError as error:
                raise HTTPException(
                    status_code=401,
                    detail="SourceCraft connection must be renewed.",
                ) from error
            except SourceCraftConnectionUnavailableError as error:
                raise HTTPException(
                    status_code=503,
                    detail="SourceCraft repository catalog is unavailable.",
                ) from error

        if repositories is None:
            if effective_repository_catalog is None:
                raise HTTPException(
                    status_code=503,
                    detail="SourceCraft repository catalog is not configured.",
                )
            try:
                repositories = await effective_repository_catalog.list_repositories()
            except SourceCraftRepositoryUnavailableError as error:
                raise HTTPException(
                    status_code=503,
                    detail="SourceCraft repository catalog is unavailable.",
                ) from error

            if any(repository.visibility != "public" for repository in repositories):
                raise HTTPException(
                    status_code=503,
                    detail="SourceCraft repository catalog is unavailable.",
                )
        repositories = tuple(
            sorted(
                repositories,
                key=lambda repository: (
                    repository.organization_slug,
                    repository.slug,
                    repository.id,
                ),
            )
        )

        # Job owner is an account subject, not a repository ACL.  Filtering by both
        # it and the freshly authorised catalog prevents another user's scan of the
        # same repository from being exposed in this browser session.
        history_jobs = await jobs.list_history_for_owner_repositories(
            user.id,
            tuple(repository.id for repository in repositories),
        )
        snapshot_ids = tuple(
            job.analysis_id
            for job in history_jobs
            if job.status in {AnalysisJobStatus.COMPLETED, AnalysisJobStatus.PARTIAL}
        )
        snapshots = await store.list_for_analysis_ids(snapshot_ids)
        snapshots_by_id = {
            stored.analysis_id: stored.snapshot
            for stored in snapshots
        }
        active_jobs_by_repository = {
            job.repository_id: job
            for job in history_jobs
            if job.status in {AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING}
        }
        terminal_jobs_by_repository = {
            job.repository_id: job
            for job in history_jobs
            if job.status not in {AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING}
        }

        return {
            "repositories": [
                _my_repository_payload(
                    repository,
                    active_jobs_by_repository.get(repository.id),
                    terminal_jobs_by_repository.get(repository.id),
                    snapshots_by_id,
                )
                for repository in repositories
            ],
            "total": len(repositories),
        }

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
            except SourceCraftConnectionRequiredError as error:
                raise HTTPException(
                    status_code=409,
                    detail="Connect SourceCraft to analyze this repository.",
                ) from error
            except SourceCraftConnectionUnavailableError as error:
                raise HTTPException(
                    status_code=503,
                    detail="SourceCraft connection is unavailable.",
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
        """Возвращает статус владельцу либо готового public-снимка планировщика."""

        normalized_id = _normalize_analysis_id(analysis_id)
        public_snapshot = await _public_scheduled_snapshot(
            analysis_id=normalized_id,
            analysis_store=store,
            job_store=jobs,
            repository_catalog=effective_repository_catalog,
        )
        if public_snapshot is not None:
            job, snapshot = public_snapshot
            return _analysis_job_status_payload(job, snapshot)
        job = await _authorized_job(request, jobs, effective_principal_provider, normalized_id)
        snapshot = await store.get(normalized_id)
        return _analysis_job_status_payload(job, snapshot)

    @app.get("/api/v1/analyses/{analysis_id}/report", tags=["reports"])
    async def get_report(analysis_id: str, request: Request) -> dict[str, object]:
        """Возвращает отчёт владельцу либо public-снимок системного планировщика."""

        normalized_id = _normalize_analysis_id(analysis_id)
        public_snapshot = await _public_scheduled_snapshot(
            analysis_id=normalized_id,
            analysis_store=store,
            job_store=jobs,
            repository_catalog=effective_repository_catalog,
        )
        if public_snapshot is None:
            await _authorized_job(request, jobs, effective_principal_provider, normalized_id)
            snapshot = await _require_snapshot(store, normalized_id)
            badge_available = (
                await _public_badge_snapshot(snapshot, effective_repository_catalog)
                is not None
            )
        else:
            _, snapshot = public_snapshot
            badge_available = True
        report = dict(snapshot.report)
        report["badgeAvailable"] = badge_available
        return report

    @app.get(
        "/api/v1/analyses/{analysis_id}/report.md",
        tags=["reports"],
        response_class=PlainTextResponse,
    )
    async def get_markdown_report(analysis_id: str, request: Request) -> PlainTextResponse:
        """Возвращает Markdown владельцу либо public-снимок планировщика."""

        normalized_id = _normalize_analysis_id(analysis_id)
        public_snapshot = await _public_scheduled_snapshot(
            analysis_id=normalized_id,
            analysis_store=store,
            job_store=jobs,
            repository_catalog=effective_repository_catalog,
        )
        if public_snapshot is None:
            await _authorized_job(request, jobs, effective_principal_provider, normalized_id)
            snapshot = await _require_snapshot(store, normalized_id)
        else:
            _, snapshot = public_snapshot
        return PlainTextResponse(snapshot.markdown, media_type="text/markdown")

    @app.get(
        "/api/v1/analyses/{analysis_id}/badge.svg",
        tags=["badges"],
        response_class=Response,
    )
    async def get_analysis_badge(analysis_id: str) -> Response:
        """Возвращает публичный SVG-бейдж для встраивания в README."""

        try:
            normalized_id = normalize_analysis_id(analysis_id)
        except ValueError:
            return _badge_response(
                render_score_badge(value_override="unknown", color_override="#6a737d")
            )

        snapshot = await _public_badge_snapshot(
            await store.get(normalized_id),
            effective_repository_catalog,
        )
        if snapshot is None:
            return _badge_response(
                render_score_badge(value_override="unknown", color_override="#6a737d")
            )
        return _badge_response(_render_score_badge(snapshot))

    @app.get(
        "/api/v1/repositories/{organization_slug}/{repository_slug}/badge.svg",
        tags=["badges"],
        response_class=Response,
    )
    async def get_repository_badge(organization_slug: str, repository_slug: str) -> Response:
        """Возвращает публичный SVG-бейдж последнего анализа репозитория."""

        latest = await store.get_latest_for_repository_slug(organization_slug, repository_slug)
        snapshot = await _public_badge_snapshot(
            latest.snapshot if latest is not None else None,
            effective_repository_catalog,
        )
        if snapshot is None:
            return _badge_response(
                render_score_badge(value_override="unknown", color_override="#6a737d")
            )
        return _badge_response(_render_score_badge(snapshot))

    return app


def _leaderboard_page_payload(
    result: LeaderboardPage,
    *,
    include_preliminary: bool,
) -> dict[str, object]:
    """Сериализует страницу рейтинга в согласованный JSON-контракт."""

    return {
        "items": [_leaderboard_row_payload(row) for row in result.entries],
        "preliminary": (
            [_leaderboard_row_payload(row) for row in result.preliminary_entries]
            if include_preliminary
            else []
        ),
        "total": result.total,
        "preliminaryTotal": result.preliminary_total,
        "partialTotal": result.partial_total,
        "page": result.page,
        "pageSize": result.page_size,
        "languages": [{"name": facet.name, "count": facet.count} for facet in result.languages],
        "updatedAt": _format_timestamp(result.updated_at),
        "pendingCount": result.pending_count,
        "methodologyVersion": result.methodology_version,
    }


def _leaderboard_row_payload(row: LeaderboardPageRow) -> dict[str, object]:
    """Сериализует одну подтверждённую public-строку рейтинга."""

    projection = row.projection
    repository = projection.repository
    return {
        "place": row.rank,
        "analysisId": projection.analysis_id,
        "repository": {
            "id": repository.repository_id,
            "organizationSlug": repository.organization_slug,
            "repositorySlug": repository.repository_slug,
            "name": repository.name,
            "url": repository.url,
            "description": repository.description,
            "language": repository.language,
        },
        "score": projection.score,
        "coverage": projection.coverage,
        "isPreliminary": projection.is_preliminary,
        "scoreLimited": projection.score_limited,
        "likes": repository.likes,
        "lastActivityAt": _format_timestamp(repository.last_activity_at),
        "analyzedAt": _format_timestamp(projection.analyzed_at),
        "categories": [
            {
                "code": category.code,
                "label": category.label,
                "status": category.status,
                "score": category.score,
            }
            for category in projection.categories
        ],
    }


def _public_repository_health_payload(
    projection: LeaderboardSnapshotProjection,
) -> dict[str, object]:
    """Строит стабильный минимальный ответ дополнительного публичного API."""

    repository = projection.repository
    return {
        "repository": {
            "organizationSlug": repository.organization_slug,
            "repositorySlug": repository.repository_slug,
            "url": repository.url,
            "language": repository.language,
        },
        "score": projection.score,
        "coverage": projection.coverage,
        "isPreliminary": projection.is_preliminary,
        "scoreLimited": projection.score_limited,
        "analyzedAt": _format_timestamp(projection.analyzed_at),
        "methodologyVersion": projection.methodology_version,
        "categories": [
            {
                "code": category.code,
                "label": category.label,
                "status": category.status,
                "score": category.score,
            }
            for category in projection.categories
        ],
    }


def _public_history_point_payload(projection: LeaderboardSnapshotProjection) -> dict[str, object]:
    """Одна точка публичной истории: только дата, Score, покрытие, статус и методика."""

    return {
        "analyzedAt": _format_timestamp(projection.analyzed_at),
        "score": projection.score,
        "coverage": projection.coverage,
        "status": "partial" if projection.is_preliminary else "completed",
        "methodologyVersion": projection.methodology_version,
    }


def create_sourcecraft_app(
    *,
    analysis_store: AnalysisStore | None = None,
    job_store: AnalysisJobStore | None = None,
    http_client_factory: Callable[[], httpx.Client] | None = None,
    clock: Callable[[], datetime] | None = None,
    analysis_id_factory: Callable[[], str] | None = None,
    principal_provider: PrincipalProvider | None = None,
    yandex_auth_service: YandexAuthService | None = None,
) -> FastAPI:
    """Явный пользовательский запуск Activity и Issues токеном вызывающего.

    Это не вход процесса. Рабочее приложение — `create_app()`: оно берёт
    public-каталог и все шесть категорий, а сервисный токен не открывает
    private/internal. Bearer здесь не копируется в subject.
    """

    store = analysis_store or _default_analysis_store()
    jobs = job_store or (
        InMemoryAnalysisJobStore() if analysis_store is not None else _default_analysis_job_store()
    )
    open_bound_client = build_request_opener(http_client_factory)

    def open_client(context: AnalysisContext) -> SourceCraftClient:
        del context
        return open_bound_client()

    execution_service = AnalysisExecutionService(
        job_store=jobs,
        snapshot_store=store,
        ai_client=_create_ai_client(),
    )
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
        principal_provider=principal_provider,
        yandex_auth_service=yandex_auth_service,
        bind_sourcecraft_token=True,
        configure_public_repository_catalog=False,
    )




def _requires_csrf_protection(request: Request, auth: YandexAuthService | None) -> bool:
    return (
        auth is not None
        and request.method not in _SAFE_HTTP_METHODS
        and request.url.path.startswith(_API_PATH_PREFIX)
        and auth.settings.cookie_name in request.cookies
    )


def _is_trusted_browser_mutation(request: Request, expected_origin: str) -> bool:
    """Проверяет Origin и браузерный Fetch Metadata для небезопасного запроса."""
    if request.headers.get("origin") != expected_origin:
        return False

    fetch_site = request.headers.get("sec-fetch-site")
    return fetch_site in (None, "same-origin")


def _require_yandex_auth(auth: YandexAuthService | None) -> YandexAuthService:
    if auth is None:
        raise HTTPException(status_code=503, detail="Yandex authentication is not configured.")
    return auth


def _require_sourcecraft_connection_service(
    connection_service: SourceCraftConnectionService | None,
) -> SourceCraftConnectionService:
    """Не показывает форму, пока server-side vault не настроен полностью."""

    if connection_service is None:
        raise HTTPException(status_code=404, detail="SourceCraft connection is not configured.")
    return connection_service


async def _require_yandex_user(
    auth: YandexAuthService,
    request: Request,
):
    try:
        return await auth.require_user(request.cookies.get(auth.settings.cookie_name))
    except PermissionError as error:
        raise HTTPException(status_code=401, detail="Authentication required.") from error


def _repository_payload(repository: SourceCraftRepository) -> dict[str, object]:
    """Строит безопасную проекцию каталога без служебных полей SourceCraft."""

    return {
        "id": repository.id,
        "organizationSlug": repository.organization_slug,
        "repositorySlug": repository.slug,
        "name": f"{repository.organization_slug}/{repository.slug}",
        "url": repository.web_url,
        "defaultBranch": repository.default_branch or None,
        "visibility": repository.visibility,
        "language": repository.language,
        "isEmpty": repository.is_empty,
    }


def _my_repository_payload(
    repository: SourceCraftRepository,
    active_job: AnalysisJob | None,
    terminal_job: AnalysisJob | None,
    snapshots_by_id: dict[str, AnalysisSnapshot],
) -> dict[str, object]:
    """Дополняет личный каталог только безопасным состоянием своего запуска."""

    payload = _repository_payload(repository)
    payload["lastAnalysis"] = None
    payload["activeAnalysisId"] = None
    if active_job is not None:
        payload["activeAnalysisId"] = active_job.analysis_id
    if terminal_job is None:
        return payload

    analyzed_at = _format_timestamp(terminal_job.finished_at)
    score: int | float | None = None
    is_preliminary = False
    snapshot = snapshots_by_id.get(terminal_job.analysis_id)
    if snapshot is not None:
        report_analysis = snapshot.report.get("analysis")
        if isinstance(report_analysis, dict):
            candidate_timestamp = report_analysis.get("analyzedAt")
            if isinstance(candidate_timestamp, str):
                analyzed_at = candidate_timestamp
            is_preliminary = report_analysis.get("isPreliminary") is True
        candidate_score = snapshot.report.get("score")
        if isinstance(candidate_score, int | float) and not isinstance(candidate_score, bool):
            score = candidate_score

    payload["lastAnalysis"] = {
        "id": terminal_job.analysis_id,
        "status": terminal_job.status.value,
        "analyzedAt": analyzed_at,
        "score": score,
        "isPreliminary": is_preliminary,
    }
    return payload


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
    connection_service: SourceCraftConnectionService | None,
) -> AnalysisDispatcher | None:
    """Создаёт единый worker с server-side выбором personal/public режима."""

    resolver = create_sourcecraft_public_repository_resolver_from_environment()
    if resolver is None and connection_service is None:
        return None
    return InProcessAnalysisDispatcher(
        execution_service=AnalysisExecutionService(
            job_store=jobs,
            snapshot_store=store,
            ai_client=_create_ai_client(),
        ),
        analysis_planner=PersonalOrPublicAnalysisPlanner(
            connection_service=connection_service,
            public_resolver=resolver,
            public_analyzer_provider=sourcecraft_analyzer_provider,
        ),
    )


def _create_default_public_repository_catalog() -> PublicRepositoryCatalog | None:
    """Создаёт каталог списка только из явно разрешённых public-организаций."""

    return create_sourcecraft_public_repository_catalog_from_environment()


def _create_default_public_analysis_scheduler(
    *,
    dispatcher: AnalysisDispatcher | None,
    job_store: AnalysisJobStore,
    repository_catalog: PublicRepositoryCatalog | None,
) -> PublicAnalysisScheduler | None:
    """Включает durable-пересчёт только по явной production-конфигурации."""

    if not _public_analysis_scheduler_enabled():
        return None
    if dispatcher is None or repository_catalog is None:
        raise RuntimeError(
            "PUBLIC_ANALYSIS_SCHEDULER_ENABLED requires public SourceCraft analysis."
        )
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "PUBLIC_ANALYSIS_SCHEDULER_ENABLED requires DATABASE_URL for PostgreSQL."
        )
    return PublicAnalysisScheduler(
        repository_catalog=repository_catalog,
        dispatcher=dispatcher,
        job_store=job_store,
        schedule_store=PostgresAnalysisScheduleStore(database_url),
    )


def _public_analysis_scheduler_enabled() -> bool:
    """Разбирает opt-in настройку без неявного включения очереди."""

    value = os.getenv("PUBLIC_ANALYSIS_SCHEDULER_ENABLED", "").strip().lower()
    if value in {"", "0", "false", "no"}:
        return False
    if value in {"1", "true", "yes"}:
        return True
    raise RuntimeError(
        "PUBLIC_ANALYSIS_SCHEDULER_ENABLED must be one of 0, 1, false or true."
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


async def _public_scheduled_snapshot(
    *,
    analysis_id: str,
    analysis_store: AnalysisStore,
    job_store: AnalysisJobStore,
    repository_catalog: PublicRepositoryCatalog | None,
) -> tuple[AnalysisJob, AnalysisSnapshot] | None:
    """Возвращает снимок, который допустимо открыть из публичного рейтинга.

    Одной видимости репозитория недостаточно: личный запуск публичного репозитория
    всё ещё принадлежит пользователю. Публикуются только завершённые снимки,
    созданные системным планировщиком, и только пока каталог SourceCraft
    подтверждает репозиторий как public. Так ссылка из лидерборда не обходит
    изоляцию персональных анализов.
    """

    job = await job_store.get(analysis_id)
    if job is None or job.owner_subject != SYSTEM_SCHEDULER_SUBJECT:
        return None
    snapshot = await analysis_store.get(analysis_id)
    if snapshot is None:
        return None
    public_snapshot = await _public_badge_snapshot(snapshot, repository_catalog)
    if public_snapshot is None:
        return None
    repository = public_snapshot.report.get("repository")
    repository_id = repository.get("id") if isinstance(repository, dict) else None
    if not isinstance(repository_id, str) or not hmac.compare_digest(
        job.repository_id.encode("utf-8"), repository_id.encode("utf-8")
    ):
        return None
    return job, public_snapshot


def _apply_http_security_headers(response: Response, path: str) -> None:
    for name, value in _COMMON_SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)

    if path.endswith("/badge.svg"):
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cache-Control"] = "public, max-age=300, s-maxage=300"
        return

    if path.startswith("/api/v1/public/"):
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cache-Control"] = (
            "public, max-age=300, s-maxage=300"
            if response.status_code == 200
            else "no-store"
        )
        return

    if path.startswith("/api/") or path in _API_DOCUMENT_PATHS:
        response.headers.setdefault("Content-Security-Policy", _API_CONTENT_SECURITY_POLICY)
    if path.startswith(_SENSITIVE_RESPONSE_PREFIXES) and not path.endswith("/badge.svg"):
        response.headers["Cache-Control"] = "no-store"


def _badge_response(svg: str) -> Response:
    return Response(content=svg, media_type="image/svg+xml")


def _render_score_badge(snapshot: AnalysisSnapshot) -> str:
    score = snapshot.report.get("score")
    # Score лежит в диапазоне 0..100; floor(x + .5) совпадает с Math.round на .5.
    rounded_score = floor(score + 0.5) if isinstance(score, (int, float)) else None
    analysis_meta = snapshot.report.get("analysis")
    is_preliminary = bool(
        isinstance(analysis_meta, dict) and analysis_meta.get("isPreliminary")
    )
    return render_score_badge(rounded_score, is_preliminary=is_preliminary)


async def _public_badge_snapshot(
    snapshot: AnalysisSnapshot | None,
    repository_catalog: PublicRepositoryCatalog | None,
) -> AnalysisSnapshot | None:
    """Возвращает снимок только для репозитория, подтверждённого public-каталогом."""

    if snapshot is None or repository_catalog is None:
        return None
    repository = snapshot.report.get("repository")
    repository_id = repository.get("id") if isinstance(repository, dict) else None
    if not isinstance(repository_id, str) or not repository_id:
        return None
    try:
        catalog = await repository_catalog.list_repositories()
    except SourceCraftRepositoryUnavailableError:
        return None
    return (
        snapshot
        if any(
            item.id == repository_id and item.visibility == "public"
            for item in catalog
        )
        else None
    )


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


def _sourcecraft_connection_payload(status: SourceCraftConnectionStatus) -> dict[str, object]:
    """Единый browser-контракт без ciphertext и без SourceCraft PAT."""

    return {
        "connected": status.connected,
        "login": status.login,
        "connectedAt": _format_timestamp(status.connected_at),
    }


app = create_app(
    yandex_auth_service=create_yandex_auth_service_from_environment(
        os.getenv("DATABASE_URL"),
    ),
    sourcecraft_connection_service=create_sourcecraft_connection_service_from_environment(
        os.getenv("DATABASE_URL"),
    ),
)
