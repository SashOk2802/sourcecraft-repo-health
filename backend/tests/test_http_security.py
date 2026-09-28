"""Проверяет browser-защиту HTTP API и намеренно закрытый CORS."""

from __future__ import annotations

import unittest

import httpx

from backend.app.analysis import InMemoryAnalysisJobStore, InMemoryAnalysisStore
from backend.app.main import create_app


class HttpSecurityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            job_store=InMemoryAnalysisJobStore(),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=self.app,
                raise_app_exceptions=False,
            ),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_api_response_has_browser_security_headers(self) -> None:
        response = await self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(
            response.headers["permissions-policy"],
            "camera=(), geolocation=(), microphone=()",
        )
        self.assertEqual(response.headers["cross-origin-resource-policy"], "same-origin")
        self.assertEqual(
            response.headers["content-security-policy"],
            "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )

    async def test_error_response_has_browser_security_headers(self) -> None:
        response = await self.client.get("/api/v1/analyses/not-a-valid-analysis-id")

        # Без principal provider endpoint намеренно закрыт с 503 до проверки ID.
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["content-security-policy"], _api_csp())

    async def test_unexpected_server_error_has_browser_security_headers(self) -> None:
        @self.app.get("/api/v1/test-unexpected-error")
        async def raise_unexpected_error() -> None:
            raise RuntimeError("test-only unexpected error")

        response = await self.client.get("/api/v1/test-unexpected-error")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.text, "Internal Server Error")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["content-security-policy"], _api_csp())

    async def test_sensitive_auth_response_is_never_cacheable(self) -> None:
        response = await self.client.get("/api/v1/auth/yandex/start")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_personal_analysis_responses_are_never_cacheable(self) -> None:
        requests = (
            ("GET", "/api/v1/me"),
            ("GET", "/api/v1/connections/sourcecraft"),
            ("GET", "/api/v1/me/repositories"),
            ("POST", "/api/v1/repositories/repo-1/analyses"),
            ("GET", "/api/v1/analyses/an-1"),
            ("GET", "/api/v1/analyses/an-1/report"),
            ("GET", "/api/v1/analyses/an-1/report.md"),
        )

        for method, path in requests:
            with self.subTest(method=method, path=path):
                response = await self.client.request(method, path)

                self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_cross_origin_site_is_not_given_api_access(self) -> None:
        response = await self.client.get(
            "/api/v1/health",
            headers={"Origin": "https://attacker.example"},
        )
        preflight = await self.client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "POST",
            },
        )

        for checked_response in (response, preflight):
            self.assertNotIn("access-control-allow-origin", checked_response.headers)
            self.assertNotIn("access-control-allow-credentials", checked_response.headers)

    async def test_interactive_docs_keep_working_without_api_csp(self) -> None:
        response = await self.client.get("/docs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertNotIn("content-security-policy", response.headers)


def _api_csp() -> str:
    return "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
