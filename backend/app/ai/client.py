"""HTTP-клиент для Yandex Foundation Models API (Yandex AI Studio)."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
_DEFAULT_MODEL = "yandexgpt-lite:latest"
_DEFAULT_TIMEOUT = 15.0
_MAX_TOKENS = 800


class YandexAiClient:
    """Минимальный клиент Yandex Foundation Models API.

    При любой сетевой или API-ошибке возвращает None — анализ не падает.
    Токен не логируется; в лог попадает только код ошибки.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = _DEFAULT_MODEL,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds

    async def complete(self, system_prompt: str, user_prompt: str) -> str | None:
        """Отправляет запрос к Foundation Models API и возвращает текст ответа или None."""
        payload = {
            "modelUri": f"ds://{self._model}" if self._model.startswith("ds/") else self._model,
            "completionOptions": {
                "stream": False,
                "temperature": 0.3,
                "maxTokens": str(_MAX_TOKENS),
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Api-Key {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                response = await http.post(_API_URL, json=payload, headers=headers)
        except httpx.TimeoutException:
            logger.warning("Yandex AI Studio request timed out.")
            return None
        except httpx.RequestError as exc:
            logger.warning("Yandex AI Studio network error: %s", type(exc).__name__)
            return None

        if response.status_code != 200:
            logger.warning(
                "Yandex AI Studio returned HTTP %d.",
                response.status_code,
            )
            return None

        try:
            data = response.json()
            return data["result"]["alternatives"][0]["message"]["text"]
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("Yandex AI Studio unexpected response shape: %s", type(exc).__name__)
            return None


def create_client_from_environment() -> YandexAiClient | None:
    """Создаёт клиент из переменных окружения или возвращает None, если ключ не задан."""
    api_key = os.environ.get("YANDEX_AI_STUDIO_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.environ.get("YANDEX_AI_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    try:
        from backend.app._env import env_float

        timeout = env_float("YANDEX_AI_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT)
    except Exception:  # noqa: BLE001
        timeout = _DEFAULT_TIMEOUT
    return YandexAiClient(api_key, model=model, timeout_seconds=timeout)
