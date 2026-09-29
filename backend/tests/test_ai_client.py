"""Тесты HTTP-клиента Yandex AI Studio."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from backend.app.ai.client import YandexAiClient, create_client_from_environment


class TestYandexAiClientInit(unittest.TestCase):
    def test_empty_api_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            YandexAiClient("")

    def test_whitespace_api_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            YandexAiClient("   ")

    def test_valid_key_creates_client(self) -> None:
        client = YandexAiClient("test-key")
        self.assertIsNotNone(client)


class TestYandexAiClientComplete(unittest.IsolatedAsyncioTestCase):
    def _make_response(self, status_code: int, json_body: object) -> httpx.Response:
        return httpx.Response(status_code, json=json_body)

    async def test_successful_response_returns_text(self) -> None:
        client = YandexAiClient("key", model="yandexgpt-lite:latest", timeout_seconds=5.0)
        mock_response = self._make_response(
            200,
            {
                "result": {
                    "alternatives": [
                        {"message": {"role": "assistant", "text": "1. Шаг первый\n2. Шаг второй"}}
                    ]
                }
            },
        )
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_http

            result = await client.complete("system", "user")

        self.assertEqual(result, "1. Шаг первый\n2. Шаг второй")

    async def test_timeout_returns_none(self) -> None:
        client = YandexAiClient("key", timeout_seconds=1.0)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_cls.return_value = mock_http

            result = await client.complete("system", "user")

        self.assertIsNone(result)

    async def test_non_200_response_returns_none(self) -> None:
        client = YandexAiClient("key")
        mock_response = self._make_response(401, {"error": "Unauthorized"})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_http

            result = await client.complete("system", "user")

        self.assertIsNone(result)

    async def test_malformed_response_returns_none(self) -> None:
        client = YandexAiClient("key")
        mock_response = self._make_response(200, {"unexpected": "shape"})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_http

            result = await client.complete("system", "user")

        self.assertIsNone(result)

    async def test_network_error_returns_none(self) -> None:
        client = YandexAiClient("key")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(
                side_effect=httpx.RequestError("connection refused", request=MagicMock())
            )
            mock_cls.return_value = mock_http

            result = await client.complete("system", "user")

        self.assertIsNone(result)


class TestCreateClientFromEnvironment(unittest.TestCase):
    def test_no_key_returns_none(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = create_client_from_environment()
        self.assertIsNone(result)

    def test_empty_key_returns_none(self) -> None:
        with patch.dict("os.environ", {"YANDEX_AI_STUDIO_API_KEY": "   "}):
            result = create_client_from_environment()
        self.assertIsNone(result)

    def test_valid_key_returns_client(self) -> None:
        with patch.dict("os.environ", {"YANDEX_AI_STUDIO_API_KEY": "real-api-key"}):
            result = create_client_from_environment()
        self.assertIsNotNone(result)

    def test_custom_model_from_env(self) -> None:
        with patch.dict(
            "os.environ",
            {"YANDEX_AI_STUDIO_API_KEY": "key", "YANDEX_AI_MODEL": "yandexgpt:latest"},
        ):
            client = create_client_from_environment()
        self.assertIsNotNone(client)
        self.assertEqual(client._model, "yandexgpt:latest")  # type: ignore[union-attr]
