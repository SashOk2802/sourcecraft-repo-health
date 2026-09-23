"""Минимальный HTTP-клиент SourceCraft без логирования токенов."""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote, urlsplit

import httpx

_SOURCECRAFT_API_HOST = "api.sourcecraft.tech"


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
        Метод чистый и не требует экземпляра клиента: сам URL секретов не несёт.
        """
        parsed = urlsplit(web_url or "")
        host = parsed.hostname if parsed.scheme == "https" and parsed.hostname else _SOURCECRAFT_API_HOST
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
            or parsed.hostname != _SOURCECRAFT_API_HOST
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
