from __future__ import annotations

import unittest

import httpx

from backend.app.integrations.sourcecraft import (
    SourceCraftAuthenticationError,
    SourceCraftClient,
    SourceCraftRateLimitError,
    SourceCraftTimeoutError,
)


class SourceCraftClientTest(unittest.TestCase):
    """Проверяет клиента без сети через искусственный HTTP transport."""

    def test_get_json_sends_bearer_token_and_returns_object(self) -> None:
        secret_token = "token-that-must-not-appear-in-errors"

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], f"Bearer {secret_token}")
            self.assertEqual(request.url.path, "/user")
            return httpx.Response(200, json={"id": "user-42"})

        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        )
        client = SourceCraftClient(secret_token, http_client=http_client)

        self.assertEqual(client.get_json("/user"), {"id": "user-42"})

    def test_authentication_error_does_not_include_token(self) -> None:
        secret_token = "token-that-must-not-appear-in-errors"
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(lambda request: httpx.Response(401)),
        )
        client = SourceCraftClient(secret_token, http_client=http_client)

        with self.assertRaises(SourceCraftAuthenticationError) as raised:
            client.get_json("/user")

        self.assertNotIn(secret_token, str(raised.exception))

    def test_rate_limit_exposes_retry_after_without_retrying(self) -> None:
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(429, headers={"Retry-After": "30"})
            ),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaises(SourceCraftRateLimitError) as raised:
            client.get_json("/user")

        self.assertEqual(raised.exception.retry_after_seconds, 30)

    def test_timeout_is_exposed_as_a_sourcecraft_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow response", request=request)

        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaises(SourceCraftTimeoutError):
            client.get_json("/user")
