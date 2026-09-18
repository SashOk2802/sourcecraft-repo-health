from __future__ import annotations

import unittest

import httpx

from backend.app.integrations.sourcecraft import (
    SourceCraftAuthenticationError,
    SourceCraftClient,
    SourceCraftNetworkError,
    SourceCraftRateLimitError,
    SourceCraftRequestError,
    SourceCraftResponseError,
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

    def test_forbidden_error_is_an_authentication_error(self) -> None:
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(lambda request: httpx.Response(403)),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaises(SourceCraftAuthenticationError):
            client.get_json("/user")

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

    def test_rate_limit_without_numeric_retry_after_keeps_unknown_wait_time(self) -> None:
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(429, headers={"Retry-After": "later"})
            ),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaises(SourceCraftRateLimitError) as raised:
            client.get_json("/user")

        self.assertIsNone(raised.exception.retry_after_seconds)

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

    def test_network_error_is_exposed_without_token(self) -> None:
        secret_token = "token-that-must-not-appear-in-errors"

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network unavailable", request=request)

        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        )
        client = SourceCraftClient(secret_token, http_client=http_client)

        with self.assertRaises(SourceCraftNetworkError) as raised:
            client.get_json("/user")

        self.assertNotIn(secret_token, str(raised.exception))

    def test_unsafe_paths_are_rejected_before_request_and_token_is_not_sent(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"unexpected": "request"})

        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        for unsafe_path in ("https://attacker.example/data", "//attacker.example/data", "data"):
            with self.subTest(path=unsafe_path), self.assertRaises(SourceCraftRequestError):
                client.get_json(unsafe_path)

        self.assertEqual(requests, [])

    def test_untrusted_base_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "official HTTPS API host"):
            SourceCraftClient("test-token", base_url="https://attacker.example")

    def test_redirect_is_rejected_without_sending_token_to_redirect_target(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                302,
                headers={"Location": "https://attacker.example/stolen-token"},
            )

        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            follow_redirects=True,
            transport=httpx.MockTransport(handler),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaisesRegex(SourceCraftResponseError, "redirect"):
            client.get_json("/user")

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "api.sourcecraft.tech")

    def test_invalid_json_becomes_sourcecraft_response_error(self) -> None:
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"<html>")),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaises(SourceCraftResponseError):
            client.get_json("/user")

    def test_scalar_json_becomes_sourcecraft_response_error(self) -> None:
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json="unexpected")),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaises(SourceCraftResponseError):
            client.get_json("/user")

    def test_unexpected_http_error_becomes_sourcecraft_response_error(self) -> None:
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaises(SourceCraftResponseError):
            client.get_json("/user")

    def test_empty_token_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            SourceCraftClient("")

    def test_close_closes_only_http_client_created_by_sourcecraft_client(self) -> None:
        with SourceCraftClient("test-token") as client:
            owned_http_client = client._http_client
            self.assertFalse(owned_http_client.is_closed)

        self.assertTrue(owned_http_client.is_closed)

        injected_http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )
        client_with_injected_http_client = SourceCraftClient(
            "test-token",
            http_client=injected_http_client,
        )
        client_with_injected_http_client.close()

        self.assertFalse(injected_http_client.is_closed)
        injected_http_client.close()
