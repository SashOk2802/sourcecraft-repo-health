"""Проверяет production smoke без сетевых запросов."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from scripts.check_production_deployment import (
    DeploymentSmokeError,
    HttpResult,
    check_production_deployment,
    main,
)


def response(
    status: int,
    body: bytes = b"",
    *,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> HttpResult:
    values = {
        "content-type": content_type,
        "cache-control": "no-store",
        **(headers or {}),
    }
    return HttpResult(status, values, body)


def healthy_responses() -> dict[str, HttpResult]:
    return {
        "/": response(
            200,
            b"<!doctype html><script src='/assets/index-safe.js'></script>",
            content_type="text/html; charset=utf-8",
            headers={
                "content-security-policy": "default-src 'self'",
                "strict-transport-security": "max-age=31536000",
                "referrer-policy": "strict-origin-when-cross-origin",
                "permissions-policy": "camera=()",
                "x-content-type-options": "nosniff",
            },
        ),
        "/@vite/client": response(404),
        "/src/main.tsx": response(404),
        "/api/v1/health": response(200, b'{"status":"ok"}'),
        "/api/v1/methodology": response(200, b"{}"),
        "/api/v1/leaderboard": response(200, b'{"items":[]}'),
        "/api/v1/me": response(401, b'{"detail":"Authentication required."}'),
        "/api/v1/me/repositories": response(
            401, b'{"detail":"Authentication required."}'
        ),
    }


class FakeFetcher:
    def __init__(self, responses: dict[str, HttpResult]) -> None:
        self.responses = responses
        self.paths: list[str] = []

    def __call__(self, url: str, timeout_seconds: int) -> HttpResult:
        self.paths.append(urlsplit(url).path)
        if timeout_seconds != 7:
            raise AssertionError("unexpected timeout")
        return self.responses[urlsplit(url).path]


class ProductionDeploymentSmokeTest(unittest.TestCase):
    def test_healthy_production_surface_passes(self) -> None:
        fetcher = FakeFetcher(healthy_responses())

        failures = check_production_deployment(
            "https://repo-health.example/",
            timeout_seconds=7,
            fetcher=fetcher,
        )

        self.assertEqual(failures, ())
        self.assertEqual(
            fetcher.paths,
            [
                "/",
                "/@vite/client",
                "/src/main.tsx",
                "/api/v1/health",
                "/api/v1/methodology",
                "/api/v1/leaderboard",
                "/api/v1/me",
                "/api/v1/me/repositories",
            ],
        )

    def test_dev_server_unavailable_catalog_and_open_personal_api_fail(self) -> None:
        responses = healthy_responses()
        responses["/"] = response(
            200,
            b'<script type="module" src="/@vite/client"></script>'
            b'<script type="module" src="/src/main.tsx"></script>',
            content_type="text/html",
        )
        responses["/@vite/client"] = response(
            200,
            b'import RefreshRuntime from "/node_modules/.vite/runtime";',
            content_type="text/javascript",
        )
        responses["/src/main.tsx"] = response(
            200,
            b'import React from "/node_modules/react";',
            content_type="text/javascript",
        )
        responses["/api/v1/leaderboard"] = response(503, b"private-response-marker")
        responses["/api/v1/me"] = response(200, b"private-user-marker")

        failures = check_production_deployment(
            "https://repo-health.example",
            timeout_seconds=7,
            fetcher=FakeFetcher(responses),
        )

        self.assertEqual(
            failures,
            (
                "vite_development_html",
                "root_csp_missing",
                "root_hsts_missing",
                "root_referrer_policy_missing",
                "root_permissions_policy_missing",
                "root_nosniff_missing",
                "vite_client_exposed",
                "source_tree_exposed",
                "leaderboard_unavailable",
                "personal_session_not_protected",
            ),
        )
        self.assertNotIn("private", repr(failures))

    def test_network_exception_is_replaced_with_stable_code(self) -> None:
        marker = "private-network-marker"

        def fetcher(_url: str, _timeout_seconds: int) -> HttpResult:
            raise OSError(marker)

        failures = check_production_deployment(
            "https://repo-health.example",
            timeout_seconds=7,
            fetcher=fetcher,
        )

        self.assertEqual(len(failures), 8)
        self.assertTrue(all(failure.endswith("_request_failed") for failure in failures))
        self.assertNotIn(marker, repr(failures))

    def test_unsafe_base_urls_and_timeout_are_rejected_before_network(self) -> None:
        urls = (
            "http://repo-health.example",
            "https://user@repo-health.example",
            "https://repo-health.example/path",
            "https://repo-health.example?token=secret",
            "https://localhost",
            "https://127.0.0.1",
            "https://repo-health.example:8443",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaisesRegex(
                DeploymentSmokeError, "base_url_invalid"
            ):
                check_production_deployment(url, fetcher=lambda *_args: self.fail())
        with self.assertRaisesRegex(DeploymentSmokeError, "timeout_invalid"):
            check_production_deployment(
                "https://repo-health.example",
                timeout_seconds=0,
                fetcher=lambda *_args: self.fail(),
            )

    def test_cli_does_not_echo_url_or_exception(self) -> None:
        marker = "private-host-marker"
        errors = io.StringIO()
        with (
            patch(
                "scripts.check_production_deployment.check_production_deployment",
                side_effect=OSError(marker),
            ),
            redirect_stderr(errors),
        ):
            exit_code = main([f"https://{marker}.example"])

        self.assertEqual(exit_code, 2)
        self.assertNotIn(marker, errors.getvalue())

    def test_manual_workflow_uses_repository_variable_without_checkout_credentials(self) -> None:
        workflow = (
            Path(__file__).parents[2] / ".github" / "workflows" / "deployment-smoke.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("DEMO_BASE_URL: ${{ vars.DEMO_BASE_URL }}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn('python scripts/check_production_deployment.py "$DEMO_BASE_URL"', workflow)
        self.assertNotIn("https://", workflow)
