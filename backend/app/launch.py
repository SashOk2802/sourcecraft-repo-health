"""Сборка запуска: кто спрашивает, какой репозиторий и каким токеном.

AnalysisPrincipal и AnalysisContext секретов не хранят. Владелец задания — subject
сессии Яндекс ID. Токен SourceCraft живёт только в contextvar задачи запроса и
нужен, чтобы прочитать репозиторий. Диспетчер копирует контекст в фоновую задачу,
а asyncio.to_thread копирует его в поток collect.

Токен — это Bearer, который прислал сам вызывающий. Переменная окружения
процесса здесь не читается: иначе приватный репозиторий открылся бы сервисным
токеном от имени любого клиента.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
from fastapi import Request

from backend.app.analysis.dispatch import AnalysisPrincipal
from backend.app.contracts import AnalysisContext, RepositoryRef
from backend.app.integrations.sourcecraft import (
    SourceCraftAuthenticationError,
    SourceCraftClient,
    SourceCraftClientError,
    SourceCraftNetworkError,
    SourceCraftRateLimitError,
    SourceCraftResponseError,
    SourceCraftTimeoutError,
)

# Контрольный период методик Activity и Issues: 180 дней до момента анализа.
ANALYSIS_WINDOW = timedelta(days=180)
_BRANCH_PAGE_SIZE = 100
# Тот же потолок, что у SourceCraftClient.get_paginated_objects: новый page token
# на каждой странице не должен крутить запрос до создания задания.
_BRANCH_MAX_PAGES = 100
# Полный SHA-1 (40 hex) или SHA-256 (64 hex). Короткий префикс и имя ветки не подходят.
_FULL_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")

_request_token: ContextVar[str | None] = ContextVar("sourcecraft_request_token", default=None)


class RepositorySnapshotError(RuntimeError):
    """Ответ SourceCraft нельзя превратить в контекст. Текст без секретов и PII."""


class AnalysisLaunchError(Exception):
    """Ошибка запуска с фиксированным HTTP-текстом, без тела ответа SourceCraft."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds


def current_request_token() -> str:
    """Возвращает токен текущего запроса. В principal и в контекст анализа он не входит."""

    token = _request_token.get()
    if not token:
        raise PermissionError("SourceCraft token is not bound to this request")
    return token


def clear_request_token() -> None:
    """Снимает токен с задачи запроса после того, как фоновая задача уже скопировала контекст."""

    _request_token.set(None)


def build_request_opener(
    http_client_factory: Callable[[], httpx.Client] | None = None,
) -> Callable[[], SourceCraftClient]:
    """Клиент на каждый вызов. Токен читается в момент вызова, не при сборке."""

    def open_bound() -> SourceCraftClient:
        token = current_request_token()
        if http_client_factory is None:
            return SourceCraftClient(token)
        return _AttachedClient(token, http_client_factory())

    return open_bound


open_request_sourcecraft_client = build_request_opener()


def bind_request_token(request: Request) -> None:
    """Кладёт Bearer SourceCraft в contextvar. В principal и в отчёт он не входит."""

    _request_token.set(_bearer_token(request))


class SourceCraftRepositoryContextResolver:
    """Проверяет доступ токеном вызывающего и собирает AnalysisContext.

    principal — subject сессии и с токеном не сравнивается. Доступ — это ответ
    SourceCraft на чтение репозитория этим Bearer. Отказ 401 или 403 не создаёт
    задание. Этот resolver не является входом процесса: рабочее приложение
    пускает только public-репозитории из настроенного каталога.
    """

    def __init__(
        self,
        open_client: Callable[[], SourceCraftClient],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._open_client = open_client
        self._clock = clock or _utc_now

    async def resolve(
        self,
        repository_id: str,
        principal: AnalysisPrincipal,
    ) -> AnalysisContext:
        _require_bound_principal(principal)
        return await asyncio.to_thread(self._resolve_sync, repository_id)

    def _resolve_sync(self, repository_id: str) -> AnalysisContext:
        client = self._open_client()
        try:
            try:
                return self._load(client, repository_id)
            except RepositorySnapshotError as error:
                raise AnalysisLaunchError(
                    502,
                    "SourceCraft returned an unusable response.",
                ) from error
        finally:
            client.close()

    def _load(self, client: SourceCraftClient, repository_id: str) -> AnalysisContext:
        repository_path = _repository_by_id_path(repository_id)
        payload = _read_object(client, repository_path, missing_repository=True)
        repository, default_branch, is_empty = _repository_ref(repository_id, payload)
        analyzed_at = self._analyzed_at()
        return AnalysisContext(
            repository=repository,
            commit_sha=_commit_sha(client, repository_path, default_branch, is_empty),
            analyzed_at=analyzed_at,
            period_start=analyzed_at - ANALYSIS_WINDOW,
            period_end=analyzed_at,
        )

    def _analyzed_at(self) -> datetime:
        moment = self._clock()
        if moment.tzinfo is None:
            raise RepositorySnapshotError("analysis clock must be timezone-aware")
        return moment


class _AttachedClient(SourceCraftClient):
    """Закрывает пул, который передали снаружи: базовый close() его не трогает."""

    def __init__(self, token: str, http_client: httpx.Client) -> None:
        super().__init__(token, http_client=http_client)
        self._attached_http_client = http_client

    def close(self) -> None:
        super().close()
        self._attached_http_client.close()


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if header is None:
        raise PermissionError("authentication required")
    scheme, separator, credential = header.partition(" ")
    if separator != " " or scheme.lower() != "bearer":
        raise PermissionError("authentication required")
    token = credential.strip()
    if not token or token != credential or any(character.isspace() for character in token):
        raise PermissionError("authentication required")
    return token


def _repository_by_id_path(repository_id: str) -> str:
    if not repository_id.strip():
        raise LookupError("repository id must not be empty")
    return f"/repos/id:{quote(repository_id, safe='')}"


def _repository_ref(
    repository_id: str,
    payload: dict[object, object],
) -> tuple[RepositoryRef, str, bool]:
    if payload.get("id") != repository_id:
        raise RepositorySnapshotError("repository id does not match the SourceCraft response")
    organization = payload.get("organization")
    org_slug = organization.get("slug") if isinstance(organization, dict) else None
    repo_slug = payload.get("slug")
    if not isinstance(org_slug, str) or not isinstance(repo_slug, str):
        raise RepositorySnapshotError("SourceCraft repository identity is incomplete")
    org_slug = org_slug.strip()
    repo_slug = repo_slug.strip()
    if not org_slug or not repo_slug:
        raise RepositorySnapshotError("SourceCraft repository identity is incomplete")
    web_url = payload.get("web_url")
    default_branch = payload.get("default_branch")
    if isinstance(web_url, str):
        web_url = web_url.strip() or None
    else:
        web_url = None
    return (
        RepositoryRef(
            id=repository_id,
            organization_slug=org_slug,
            repository_slug=repo_slug,
            web_url=web_url,
        ),
        default_branch.strip() if isinstance(default_branch, str) else "",
        payload.get("is_empty") is True,
    )


def _commit_sha(
    client: SourceCraftClient,
    repository_path: str,
    default_branch: str,
    is_empty: bool,
) -> str:
    if is_empty:
        return ""
    if not default_branch.strip():
        raise RepositorySnapshotError("SourceCraft repository has no default branch")

    params: dict[str, str | int] = {"page_size": _BRANCH_PAGE_SIZE}
    seen_page_tokens: set[str] = set()
    for _page in range(_BRANCH_MAX_PAGES):
        payload = _read_object(client, f"{repository_path}/branches", params=params)
        branches = payload.get("branches")
        if not isinstance(branches, list):
            raise RepositorySnapshotError("SourceCraft branches response must be a list")
        for branch in branches:
            digest = _branch_commit_hash(branch, default_branch)
            if digest is not None:
                return digest
        next_token = payload.get("next_page_token") or ""
        if not isinstance(next_token, str) or not next_token or next_token in seen_page_tokens:
            break
        seen_page_tokens.add(next_token)
        params["page_token"] = next_token
    else:
        raise RepositorySnapshotError("SourceCraft branch page limit exceeded")
    raise RepositorySnapshotError("default branch commit is unavailable")


def _require_bound_principal(principal: AnalysisPrincipal) -> None:
    """Не ходит в SourceCraft, пока у запроса нет своего Bearer.

    principal — subject сессии. Он не сравнивается с токеном и в клиент не попадает.
    """

    del principal
    current_request_token()


def _read_object(
    client: SourceCraftClient,
    path: str,
    *,
    params: dict[str, str | int] | None = None,
    missing_repository: bool = False,
) -> dict[object, object]:
    try:
        payload = client.get_json(path, params=params)
    except SourceCraftAuthenticationError as error:
        if error.status_code == 401:
            raise AnalysisLaunchError(401, "SourceCraft rejected the token.") from error
        raise PermissionError("repository access denied") from error
    except SourceCraftRateLimitError as error:
        raise AnalysisLaunchError(
            429,
            "SourceCraft rate limit exceeded.",
            retry_after_seconds=error.retry_after_seconds,
        ) from error
    except (SourceCraftTimeoutError, SourceCraftNetworkError) as error:
        raise AnalysisLaunchError(503, "SourceCraft is unavailable.") from error
    except SourceCraftResponseError as error:
        if missing_repository and error.status_code == 404:
            raise LookupError("repository not found") from error
        raise AnalysisLaunchError(
            502,
            "SourceCraft returned an unusable response.",
        ) from error
    except SourceCraftClientError as error:
        raise AnalysisLaunchError(
            502,
            "SourceCraft returned an unusable response.",
        ) from error
    if not isinstance(payload, dict):
        raise RepositorySnapshotError("SourceCraft response must be an object")
    return payload


def _branch_commit_hash(branch: object, default_branch: str) -> str | None:
    """Берёт только полный hash. Имя, почту и сообщение коммита в контекст не копирует.

    Непустая строка из API сама по себе не является коммитом: имя ветки и
    короткий SHA отклоняют весь снимок до создания задания.
    """

    if not isinstance(branch, dict) or branch.get("name") != default_branch:
        return None
    commit = branch.get("commit")
    if not isinstance(commit, dict):
        return None
    digest = commit.get("hash")
    if not isinstance(digest, str) or not digest.strip():
        return None
    if _FULL_COMMIT_SHA.fullmatch(digest) is None:
        raise RepositorySnapshotError("SourceCraft default branch commit SHA is invalid")
    return digest.lower()


def _utc_now() -> datetime:
    return datetime.now(UTC)
