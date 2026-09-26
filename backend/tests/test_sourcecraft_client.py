from __future__ import annotations

import unittest
from unittest.mock import patch

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

    def test_owned_client_ignores_proxy_and_ca_environment(self) -> None:
        with patch("backend.app.integrations.sourcecraft.httpx.Client") as factory:
            client = SourceCraftClient("test-token", timeout_seconds=15)

        factory.assert_called_once_with(
            base_url="https://api.sourcecraft.tech",
            timeout=15,
            follow_redirects=False,
            trust_env=False,
        )
        client.close()

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

    def test_null_json_is_available_for_endpoint_specific_mapping(self) -> None:
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=b"null",
                    headers={"Content-Type": "application/json"},
                )
            ),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        self.assertIsNone(client.get_json("/appsec/defects"))

    def test_get_paginated_objects_follows_next_page_token(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.params.get("page_token") == "page-2":
                return httpx.Response(
                    200,
                    json={"runs": [{"id": "run-2"}], "next_page_token": ""},
                )
            return httpx.Response(
                200,
                json={"runs": [{"id": "run-1"}], "next_page_token": "page-2"},
            )

        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        runs = client.get_paginated_objects(
            "/repos/example-org/example-repo/cicd/runs", items_field="runs"
        )

        self.assertEqual(runs, [{"id": "run-1"}, {"id": "run-2"}])
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].url.params["page_size"], "100")
        self.assertNotIn("page_token", requests[0].url.params)
        self.assertEqual(requests[1].url.params["page_token"], "page-2")

    def test_get_paginated_objects_rejects_repeated_page_token(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"runs": [], "next_page_token": "same-token"})

        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaisesRegex(SourceCraftResponseError, "repeated next_page_token"):
            client.get_paginated_objects(
                "/repos/example-org/example-repo/cicd/runs", items_field="runs"
            )

        self.assertEqual(len(requests), 2)

    def test_get_paginated_objects_rejects_invalid_page_shapes(self) -> None:
        invalid_payloads: tuple[object, ...] = (
            [{"id": "not-an-envelope"}],
            {"runs": {"id": "not-a-list"}},
            {"runs": ["not-an-object"]},
            {"runs": [], "next_page_token": 42},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                http_client = httpx.Client(
                    base_url="https://api.sourcecraft.tech",
                    transport=httpx.MockTransport(
                        lambda request, payload=payload: httpx.Response(200, json=payload)
                    ),
                )
                client = SourceCraftClient("test-token", http_client=http_client)

                with self.assertRaises(SourceCraftResponseError):
                    client.get_paginated_objects(
                        "/repos/example-org/example-repo/cicd/runs",
                        items_field="runs",
                    )

    def test_get_paginated_objects_stops_at_configured_page_limit(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={"runs": [], "next_page_token": f"page-{len(requests)}"},
            )

        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaisesRegex(SourceCraftResponseError, "page limit"):
            client.get_paginated_objects(
                "/repos/example-org/example-repo/cicd/runs",
                items_field="runs",
                max_pages=2,
            )

        self.assertEqual(len(requests), 2)

    def test_get_paginated_objects_validates_configuration_before_request(self) -> None:
        requests: list[httpx.Request] = []
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(200, json={"runs": []})
            ),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaises(ValueError):
            client.get_paginated_objects(
                "/repos/example-org/example-repo/cicd/runs", items_field=""
            )
        with self.assertRaises(ValueError):
            client.get_paginated_objects(
                "/repos/example-org/example-repo/cicd/runs",
                items_field="runs",
                page_size=0,
            )
        with self.assertRaises(ValueError):
            client.get_paginated_objects(
                "/repos/example-org/example-repo/cicd/runs",
                items_field="runs",
                max_pages=0,
        )

        for invalid_items_field in (None, True, 1):
            with self.subTest(invalid_items_field=invalid_items_field), self.assertRaises(TypeError):
                client.get_paginated_objects(
                    "/repos/example-org/example-repo/cicd/runs",
                    items_field=invalid_items_field,  # type: ignore[arg-type]
                )
        with self.assertRaises(ValueError):
            client.get_paginated_objects(
                "/repos/example-org/example-repo/cicd/runs", items_field="   "
            )

        for field, invalid_value in (
            ("page_size", True),
            ("page_size", 1.5),
            ("page_size", float("nan")),
            ("max_pages", False),
            ("max_pages", 2.5),
            ("max_pages", float("inf")),
        ):
            with self.subTest(field=field, invalid_value=invalid_value), self.assertRaises(TypeError):
                client.get_paginated_objects(
                    "/repos/example-org/example-repo/cicd/runs",
                    items_field="runs",
                    **{field: invalid_value},  # type: ignore[arg-type]
                )

        for invalid_params, expected_error in (
            ([("filter", "main")], TypeError),
            ({" ": "main"}, ValueError),
            ({"\x00filter": "main"}, ValueError),
            ({"filter": True}, TypeError),
            ({"filter": 1.5}, TypeError),
        ):
            with self.subTest(invalid_params=invalid_params), self.assertRaises(expected_error):
                client.get_paginated_objects(
                    "/repos/example-org/example-repo/cicd/runs",
                    items_field="runs",
                    params=invalid_params,  # type: ignore[arg-type]
                )

        self.assertEqual(requests, [])

    def test_authentication_error_does_not_include_token(self) -> None:
        secret_token = "token-that-must-not-appear-in-errors"
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(lambda request: httpx.Response(401)),
        )
        client = SourceCraftClient(secret_token, http_client=http_client)

        with self.assertRaises(SourceCraftAuthenticationError) as raised:
            client.get_json("/user")

        self.assertEqual(raised.exception.status_code, 401)
        self.assertNotIn(secret_token, str(raised.exception))

    def test_forbidden_error_is_an_authentication_error(self) -> None:
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(lambda request: httpx.Response(403)),
        )
        client = SourceCraftClient("test-token", http_client=http_client)

        with self.assertRaises(SourceCraftAuthenticationError) as raised:
            client.get_json("/user")

        self.assertEqual(raised.exception.status_code, 403)

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

    def test_injected_http_client_with_untrusted_base_url_is_rejected_before_request(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={})

        http_client = httpx.Client(
            base_url="https://attacker.example",
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaisesRegex(ValueError, "official HTTPS API host"):
            SourceCraftClient("test-token", http_client=http_client)

        self.assertEqual(requests, [])
        http_client.close()

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

        with self.assertRaises(SourceCraftResponseError) as raised:
            client.get_json("/user")

        self.assertEqual(raised.exception.status_code, 500)

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
