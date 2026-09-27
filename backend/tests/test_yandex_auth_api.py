from __future__ import annotations

import os
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from starlette.requests import Request

from backend.app.analysis import InMemoryAnalysisJobStore, InMemoryAnalysisStore
from backend.app.identity import (
    InMemoryYandexAuthStore,
    YandexAuthService,
    YandexAuthSettings,
    create_yandex_auth_service_from_environment,
)
from backend.app.identity.yandex import YandexOAuthClient
from backend.app.main import create_app


class YandexAuthApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)
        self.provider_requests: list[httpx.Request] = []
        self.settings = YandexAuthSettings(
            client_id="client-42",
            client_secret="client-secret",
            redirect_uri="http://localhost:5173/api/v1/auth/yandex/callback",
            cookie_secure=False,
        )
        oauth_client = YandexOAuthClient(
            self.settings,
            http_client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(self._provider_handler),
            ),
        )
        self.auth = YandexAuthService(
            self.settings,
            InMemoryYandexAuthStore(),
            oauth_client=oauth_client,
            clock=lambda: self.now,
        )
        self.app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            job_store=InMemoryAnalysisJobStore(),
            yandex_auth_service=self.auth,
        )

    async def test_login_uses_pkce_and_creates_opaque_server_session(self) -> None:
        async with api_client(self.app) as client:
            started = await client.get("/api/v1/auth/yandex/start")
            parameters = parse_qs(urlsplit(started.headers["location"]).query)
            callback = await client.get(
                "/api/v1/auth/yandex/callback",
                params={"code": "one-time-code", "state": parameters["state"][0]},
            )
            current_user = await client.get("/api/v1/me")

            session_token = client.cookies.get(self.settings.cookie_name)
            self.assertIsNotNone(session_token)
            principal = await self.app.state.principal_provider(
                request_with_cookie(self.settings.cookie_name, session_token),
            )

        self.assertEqual(started.status_code, 307)
        self.assertEqual(urlsplit(started.headers["location"]).scheme, "https")
        self.assertEqual(parameters["response_type"], ["code"])
        self.assertEqual(parameters["client_id"], ["client-42"])
        self.assertEqual(parameters["redirect_uri"], [self.settings.redirect_uri])
        self.assertEqual(parameters["scope"], ["login:info"])
        self.assertEqual(parameters["code_challenge_method"], ["S256"])
        self.assertEqual(len(parameters["state"][0]), 43)
        self.assertNotEqual(parameters["state"][0], parameters["code_challenge"][0])

        self.assertEqual(callback.status_code, 303)
        self.assertEqual(callback.headers["location"], "/me/repositories")
        self.assertIn("HttpOnly", callback.headers["set-cookie"])
        self.assertIn("SameSite=lax", callback.headers["set-cookie"])
        self.assertNotIn("Secure", callback.headers["set-cookie"])
        self.assertNotIn("access-token", callback.headers["set-cookie"])
        self.assertEqual(current_user.status_code, 200)
        self.assertEqual(current_user.json()["login"], "alex")
        self.assertTrue(current_user.json()["id"].startswith("user-"))
        self.assertEqual(principal.subject, current_user.json()["id"])

        token_request, profile_request = self.provider_requests
        self.assertEqual(str(token_request.url), "https://oauth.yandex.ru/token")
        self.assertIn(b"code_verifier=", token_request.content)
        self.assertNotIn(b"code_challenge", token_request.content)
        self.assertTrue(token_request.headers["authorization"].startswith("Basic "))
        self.assertEqual(str(profile_request.url), "https://login.yandex.ru/info")
        self.assertEqual(profile_request.headers["authorization"], "OAuth access-token")

    async def test_state_is_single_use_and_expired_state_never_calls_provider(self) -> None:
        async with api_client(self.app) as client:
            started = await client.get("/api/v1/auth/yandex/start")
            state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
            first = await client.get(
                "/api/v1/auth/yandex/callback",
                params={"code": "one-time-code", "state": state},
            )
            provider_call_count = len(self.provider_requests)
            repeated = await client.get(
                "/api/v1/auth/yandex/callback",
                params={"code": "one-time-code", "state": state},
            )

            expired_start = await client.get("/api/v1/auth/yandex/start")
            expired_state = parse_qs(urlsplit(expired_start.headers["location"]).query)["state"][0]
            self.now += timedelta(minutes=11)
            expired = await client.get(
                "/api/v1/auth/yandex/callback",
                params={"code": "one-time-code", "state": expired_state},
            )

        self.assertEqual(first.status_code, 303)
        self.assertEqual(repeated.status_code, 401)
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(len(self.provider_requests), provider_call_count)

    async def test_provider_failure_does_not_leak_token_or_provider_response(self) -> None:
        async with api_client(self.app) as client:
            started = await client.get("/api/v1/auth/yandex/start")
            state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
            response = await client.get(
                "/api/v1/auth/yandex/callback",
                params={"code": "provider-failure", "state": state},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {"detail": "Yandex authentication provider is unavailable."},
        )
        self.assertNotIn("provider-secret", response.text)

    async def test_logout_revokes_session_on_server(self) -> None:
        async with api_client(self.app) as client:
            await self._login(client)
            logged_out = await client.post(
                "/api/v1/auth/logout",
                headers=trusted_browser_headers(self.settings),
            )
            current_user = await client.get("/api/v1/me")

        self.assertEqual(logged_out.status_code, 204)
        self.assertIn("Max-Age=0", logged_out.headers["set-cookie"])
        self.assertEqual(current_user.status_code, 401)
        self.assertEqual(current_user.json(), {"detail": "Authentication required."})

    async def test_cross_site_logout_is_rejected_without_revoking_session(self) -> None:
        async with api_client(self.app) as client:
            await self._login(client)
            rejected = await client.post(
                "/api/v1/auth/logout",
                headers={
                    "origin": "https://attacker.example",
                    "sec-fetch-site": "cross-site",
                },
            )
            current_user = await client.get("/api/v1/me")

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.json(), {"detail": "Cross-site request rejected."})
        self.assertEqual(rejected.headers["x-content-type-options"], "nosniff")
        self.assertEqual(
            rejected.headers["content-security-policy"],
            "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.assertNotIn("set-cookie", rejected.headers)
        self.assertEqual(current_user.status_code, 200)

    async def test_unsafe_cookie_requests_require_same_origin_browser_headers(self) -> None:
        async with api_client(self.app) as client:
            await self._login(client)
            without_origin = await client.post("/api/v1/auth/logout")
            wrong_fetch_metadata = await client.post(
                "/api/v1/auth/logout",
                headers={
                    "origin": self.settings.callback_origin,
                    "sec-fetch-site": "cross-site",
                },
            )

        self.assertEqual(without_origin.status_code, 403)
        self.assertEqual(wrong_fetch_metadata.status_code, 403)

    async def test_same_origin_request_without_fetch_metadata_is_allowed(self) -> None:
        async with api_client(self.app) as client:
            await self._login(client)
            logged_out = await client.post(
                "/api/v1/auth/logout",
                headers={"origin": self.settings.callback_origin},
            )

        self.assertEqual(logged_out.status_code, 204)

    async def test_logout_without_a_cookie_remains_idempotent(self) -> None:
        async with api_client(self.app) as client:
            response = await client.post("/api/v1/auth/logout")

        self.assertEqual(response.status_code, 204)

    async def test_unconfigured_auth_endpoints_return_service_unavailable(self) -> None:
        app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            job_store=InMemoryAnalysisJobStore(),
        )

        async with api_client(app) as client:
            started = await client.get("/api/v1/auth/yandex/start")
            current_user = await client.get("/api/v1/me")

        self.assertEqual(started.status_code, 503)
        self.assertEqual(current_user.status_code, 503)

    async def _login(self, client: httpx.AsyncClient) -> None:
        started = await client.get("/api/v1/auth/yandex/start")
        state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
        completed = await client.get(
            "/api/v1/auth/yandex/callback",
            params={"code": "one-time-code", "state": state},
        )
        self.assertEqual(completed.status_code, 303)

    def _provider_handler(self, request: httpx.Request) -> httpx.Response:
        self.provider_requests.append(request)
        if request.url == httpx.URL("https://oauth.yandex.ru/token"):
            parameters = parse_qs(request.content.decode("utf-8"))
            if parameters["code"] == ["provider-failure"]:
                return httpx.Response(502, json={"access_token": "provider-secret"})
            return httpx.Response(200, json={"access_token": "access-token"})
        if request.url == httpx.URL("https://login.yandex.ru/info"):
            return httpx.Response(200, json={"id": "yandex-42", "login": "alex"})
        return httpx.Response(404)


class YandexAuthConfigurationTest(unittest.TestCase):
    def test_requires_complete_environment_configuration(self) -> None:
        with (
            patch.dict(os.environ, {"YANDEX_CLIENT_ID": "client-42"}, clear=True),
            self.assertRaisesRegex(RuntimeError, "configured together"),
        ):
            create_yandex_auth_service_from_environment(None)

    def test_rejects_untrusted_provider_hosts(self) -> None:
        with self.assertRaisesRegex(ValueError, "official HTTPS URL"):
            YandexAuthSettings(
                client_id="client-42",
                redirect_uri="https://repo-health.example/api/v1/auth/yandex/callback",
                token_url="https://attacker.example/token",
            )

    def test_canonical_callback_origin_is_used_for_browser_checks(self) -> None:
        settings = YandexAuthSettings(
            client_id="client-42",
            redirect_uri="https://Repo-Health.Example:443/api/v1/auth/yandex/callback",
        )

        self.assertEqual(settings.callback_origin, "https://repo-health.example")

    def test_rejects_unsafe_or_ambiguous_callback_urls(self) -> None:
        invalid_redirect_uris = (
            "https://repo-health.example/callback",
            "https://repo-health.example/api/v1/auth/yandex/callback?next=/",
            "https://user@repo-health.example/api/v1/auth/yandex/callback",
            "http://repo-health.example/api/v1/auth/yandex/callback",
            "http://localhost:5173/api/v1/auth/yandex/callback",
            "https://repo-health.example:0/api/v1/auth/yandex/callback",
            "https://repo-health.example/api/v1/auth/yandex/callback\nhttps://attacker.example",
        )

        for redirect_uri in invalid_redirect_uris:
            with self.subTest(redirect_uri=redirect_uri), self.assertRaisesRegex(ValueError, "redirect_uri"):
                YandexAuthSettings(client_id="client-42", redirect_uri=redirect_uri)

    def test_allows_local_http_callback_only_with_insecure_development_cookie(self) -> None:
        settings = YandexAuthSettings(
            client_id="client-42",
            redirect_uri="http://127.0.0.1:5173/api/v1/auth/yandex/callback",
            cookie_secure=False,
        )

        self.assertEqual(settings.callback_origin, "http://127.0.0.1:5173")


def request_with_cookie(cookie_name: str, cookie_value: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"cookie", f"{cookie_name}={cookie_value}".encode())],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }
    )


def trusted_browser_headers(settings: YandexAuthSettings) -> dict[str, str]:
    return {
        "origin": settings.callback_origin,
        "sec-fetch-site": "same-origin",
    }


@asynccontextmanager
async def api_client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            yield client
