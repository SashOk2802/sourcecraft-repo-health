"""Минимальный HTTP-клиент SourceCraft без логирования токенов."""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote, urlsplit

import httpx

_SOURCECRAFT_API_HOST = "api.sourcecraft.tech"

# Единственный источник правды о доверенных хостах SourceCraft: каталог
# (страницы репозиториев и git-клоны) живёт на sourcecraft.dev, REST API — на
# api.sourcecraft.tech. Оба валидатора (_validate_base_url и resolve_git_clone_url)
# сверяются именно с этим набором; новые официальные хосты добавляются только здесь.
_SOURCECRAFT_GIT_HOSTS = frozenset({_SOURCECRAFT_API_HOST, "sourcecraft.dev"})


class SourceCraftClientError(RuntimeError):
    """Базовая ошибка интеграции с SourceCraft без чувствительных данных."""


class SourceCraftAuthenticationError(SourceCraftClientError):
    """SourceCraft отклонил токен или у него нет прав на ресурс."""


class SourceCraftRateLimitError(SourceCraftClientError):
    """SourceCraft временно ограничил частоту запросов."""

    def __init__(self, retry_after_seconds: int | None) -> None:
        super().__init__("SourceCraft rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class SourceCraftTimeoutError(SourceCraftClientError):
    """SourceCraft не ответил за отведённое время."""


class SourceCraftResponseError(SourceCraftClientError):
    """SourceCraft вернул ошибочный HTTP-ответ или неожиданный JSON."""


class SourceCraftRequestError(SourceCraftClientError):
    """Клиент получил небезопасные параметры запроса."""


class SourceCraftNetworkError(SourceCraftClientError):
    """Не удалось установить соединение с SourceCraft."""


class SourceCraftClient:
    """Выполняет безопасные запросы к SourceCraft REST API.

    Токен передаётся только в HTTP-заголовке, только на официальный HTTPS-host
    SourceCraft и не включается в тексты исключений. Redirect не допускаются.
    Тот же токен используется для аутентификации git-операций с каталогом
    SourceCraft (см. ``resolve_git_clone_url``): credential path один и тот же,
    новый механизм секретов не вводится.
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.sourcecraft.tech",
        timeout_seconds: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not token:
            raise ValueError("SourceCraft token must not be empty")

        effective_base_url = str(http_client.base_url) if http_client is not None else base_url
        self._validate_base_url(effective_base_url)

        self._token = token
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            follow_redirects=False,
        )

    def close(self) -> None:
        """Закрывает HTTP-соединения, созданные самим клиентом."""

        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @staticmethod
    def resolve_git_clone_url(
        organization_slug: str,
        repository_slug: str,
        web_url: str | None,
    ) -> str:
        """Возвращает URL git-remota для клонирования репозитория.

        Git-хост выводится из каталога SourceCraft (host страницы репозитория),
        а не придумывается заново; аутентификация — тот же Bearer-PAT клиента,
        который git получает через ``http.extraheader`` (см. LocalGitRepository).
        Если каталог не отдал страницу, используется официальный API-host.
        Хост из web_url не доверяется вслепую: он обязан входить в
        ``_SOURCECRAFT_GIT_HOSTS``, иначе URL клона не конструируется вовсе.
        Метод чистый и не требует экземпляра клиента: сам URL секретов не несёт.
        """
        parsed = urlsplit(web_url or "")
        host = (
            parsed.hostname
            if parsed.scheme == "https" and parsed.hostname
            else _SOURCECRAFT_API_HOST
        )
        if host not in _SOURCECRAFT_GIT_HOSTS:
            raise SourceCraftRequestError(
                "SourceCraft git clone URL must use an official SourceCraft host"
            )
        org = quote(organization_slug, safe="")
        slug = quote(repository_slug, safe="")
        return f"https://{host}/{org}/{slug}.git"

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        """Возвращает JSON object, array или null либо типизированную ошибку."""

        self._validate_path(path)

        try:
            response = self._http_client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {self._token}"},
                follow_redirects=False,
            )
        except httpx.TimeoutException as error:
            raise SourceCraftTimeoutError("SourceCraft request timed out") from error
        except httpx.RequestError as error:
            raise SourceCraftNetworkError("SourceCraft network request failed") from error

        self._raise_for_status(response)

        try:
            payload = response.json()
        except ValueError as error:
            raise SourceCraftResponseError("SourceCraft returned invalid JSON") from error

        if payload is None:
            return None
        if not isinstance(payload, (dict, list)):
            raise SourceCraftResponseError("SourceCraft JSON must be an object, array or null")
        return payload

    def get_paginated_objects(
        self,
        path: str,
        *,
        items_field: str,
        params: Mapping[str, str | int] | None = None,
        page_size: int = 100,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        """Загружает страницы списка SourceCraft, следуя `next_page_token`.

        Ограничение на число страниц и проверка повторного токена не позволяют
        некорректному ответу платформы запустить бесконечный цикл запросов.
        """

        if not items_field:
            raise ValueError("items_field must not be empty")
        if page_size < 1:
            raise ValueError("page_size must be positive")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")

        request_params = dict(params or {})
        request_params["page_size"] = page_size
        request_params.pop("page_token", None)
        collected: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()

        for _ in range(max_pages):
            payload = self.get_json(path, params=request_params)
            if not isinstance(payload, dict):
                raise SourceCraftResponseError("SourceCraft paginated response must be an object")

            items = payload.get(items_field)
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise SourceCraftResponseError(
                    f"SourceCraft paginated response must contain an array of objects in {items_field}"
                )
            collected.extend(items)

            next_page_token = payload.get("next_page_token")
            if next_page_token in (None, ""):
                return collected
            if not isinstance(next_page_token, str):
                raise SourceCraftResponseError("SourceCraft next_page_token must be a string")
            if next_page_token in seen_tokens:
                raise SourceCraftResponseError("SourceCraft returned a repeated next_page_token")

            seen_tokens.add(next_page_token)
            request_params["page_token"] = next_page_token

        raise SourceCraftResponseError("SourceCraft pagination exceeded the configured page limit")

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_redirect:
            raise SourceCraftResponseError("SourceCraft returned a redirect, which is not allowed")

        if response.status_code in (401, 403):
            raise SourceCraftAuthenticationError(
                f"SourceCraft denied access with HTTP {response.status_code}"
            )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            retry_after_seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
            raise SourceCraftRateLimitError(retry_after_seconds)

        if response.is_error:
            raise SourceCraftResponseError(
                f"SourceCraft returned unexpected HTTP {response.status_code}"
            )

    @staticmethod
    def _validate_base_url(base_url: str) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _SOURCECRAFT_GIT_HOSTS
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("SourceCraft base URL must use the official HTTPS API host")

    @staticmethod
    def _validate_path(path: str) -> None:
        parsed = urlsplit(path)
        if (
            not path.startswith("/")
            or path.startswith("//")
            or parsed.scheme
            or parsed.netloc
        ):
            raise SourceCraftRequestError(
                "SourceCraft request path must be a relative path beginning with '/'"
            )
