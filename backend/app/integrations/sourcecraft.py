"""Минимальный HTTP-клиент SourceCraft без логирования токенов."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


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


class SourceCraftClient:
    """Выполняет безопасные запросы к SourceCraft REST API.

    Реальные endpoints и формат пагинации появятся после проверки матрицы
    возможностей. Токен передаётся только в HTTP-заголовке и не включается
    в тексты исключений.
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

        self._token = token
        self._http_client = http_client or httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
        )

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Возвращает JSON от GET endpoint или типизированную ошибку."""

        try:
            response = self._http_client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.TimeoutException as error:
            raise SourceCraftTimeoutError("SourceCraft request timed out") from error

        self._raise_for_status(response)

        try:
            payload = response.json()
        except ValueError as error:
            raise SourceCraftResponseError("SourceCraft returned invalid JSON") from error

        if not isinstance(payload, (dict, list)):
            raise SourceCraftResponseError("SourceCraft JSON must be an object or array")
        return payload

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
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
