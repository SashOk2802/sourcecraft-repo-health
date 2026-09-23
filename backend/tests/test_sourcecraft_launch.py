"""HTTP-запуск доходит до Activity и Issues, а не останавливается на 503."""

from __future__ import annotations

import asyncio
import hashlib
import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.parse import unquote

import httpx
from fastapi import Request

from backend.app.analysis import InMemoryAnalysisJobStore, InMemoryAnalysisStore
from backend.app.analysis.dispatch import AnalysisPrincipal
from backend.app.integrations.sourcecraft import (
    SourceCraftAuthenticationError,
    SourceCraftRateLimitError,
    SourceCraftResponseError,
    SourceCraftTimeoutError,
)
from backend.app.launch import (
    ANALYSIS_WINDOW,
    AnalysisLaunchError,
    SourceCraftRepositoryContextResolver,
    _request_token,
    current_request_token,
    open_request_sourcecraft_client,
    principal_from_authorization,
)
from backend.app.main import app, create_app, create_sourcecraft_app
from backend.app.scoring.engine import CATEGORY_WEIGHTS

ANALYZED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
COMMIT_SHA = "a" * 40
SECRET_EMAIL = "secret-author@example.com"
SERVICE_TOKEN = "service-secret"
USER_TOKEN = "user-pat"
OTHER_TOKEN = "other-pat"
REPOSITORY_ID = "repo-42"
REPOSITORY_BY_ID = f"/repos/id:{REPOSITORY_ID}"
BRANCHES_BY_ID = f"{REPOSITORY_BY_ID}/branches"
ANALYZER_BASE = "/repos/team/platform"
UNREACHABLE_DATABASE = "postgresql+asyncpg://127.0.0.1:1/unused"


def bound_principal(token: str = USER_TOKEN) -> AnalysisPrincipal:
    _request_token.set(token)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return AnalysisPrincipal(f"token:{digest}")


def isolated_sourcecraft_app(**kwargs: object):
    """Хранилища в памяти: CI задаёт DATABASE_URL, и тест не должен открывать PostgreSQL."""

    return create_sourcecraft_app(
        analysis_store=InMemoryAnalysisStore(),
        job_store=InMemoryAnalysisJobStore(),
        **kwargs,
    )


def moment(days_before_analysis: int) -> str:
    return (ANALYZED_AT - timedelta(days=days_before_analysis)).isoformat()


def repository_payload(*, is_empty: bool = False, repository_id: str = REPOSITORY_ID) -> dict:
    return {
        "id": repository_id,
        "slug": "platform",
        "visibility": "private",
        "web_url": "https://sourcecraft.dev/team/platform",
        "default_branch": "main",
        "is_empty": is_empty,
        "last_updated": moment(2),
        "organization": {"id": "org-1", "slug": "team"},
    }


def branch(name: str, digest: str) -> dict:
    return {
        "name": name,
        "commit": {
            "hash": digest,
            "message": "do not copy",
            "author": {"name": "Hidden Name", "email": SECRET_EMAIL},
        },
    }


def issue(slug: str, *, created_days_ago: int, updated_days_ago: int, status_type: str) -> dict:
    payload = {
        "slug": slug,
        "title": slug,
        "created_at": moment(created_days_ago),
        "updated_at": moment(updated_days_ago),
        "status": {"status_type": status_type},
    }
    if status_type == "completed":
        payload["completed_at"] = moment(updated_days_ago)
    return payload


class ScriptedClient:
    """Подменяет SourceCraft в resolver: сеть не нужна, закрытие видно тесту."""

    def __init__(self, steps: list[object]) -> None:
        self._steps = list(steps)
        self.calls: list[str] = []
        self.closed = False

    def get_json(self, path: str, *, params: object = None) -> object:
        del params
        self.calls.append(path)
        if not self._steps:
            raise AssertionError(path)
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def close(self) -> None:
        self.closed = True


class SourceCraftResolverTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        _request_token.set(None)

    async def test_private_repository_uses_caller_token_and_drops_commit_pii(self) -> None:
        client = ScriptedClient(
            [
                repository_payload(),
                {
                    "branches": [branch("other", "b" * 40)],
                    "next_page_token": "next",
                },
                {
                    "branches": [branch("main", COMMIT_SHA)],
                    "next_page_token": "",
                },
            ]
        )
        resolver = SourceCraftRepositoryContextResolver(
            lambda: client,
            clock=lambda: ANALYZED_AT,
        )

        context = await resolver.resolve(REPOSITORY_ID, bound_principal())

        self.assertTrue(client.closed)
        self.assertEqual(
            client.calls,
            [REPOSITORY_BY_ID, BRANCHES_BY_ID, BRANCHES_BY_ID],
        )
        self.assertEqual(context.repository.organization_slug, "team")
        self.assertEqual(context.repository.repository_slug, "platform")
        self.assertEqual(context.commit_sha, COMMIT_SHA)
        self.assertEqual(context.period_end, ANALYZED_AT)
        self.assertEqual(context.period_start, ANALYZED_AT - ANALYSIS_WINDOW)
        self.assertNotIn(SECRET_EMAIL, repr(context))
        self.assertNotIn("Hidden Name", repr(context))

    async def test_empty_repository_has_no_commit_and_skips_branches(self) -> None:
        client = ScriptedClient([repository_payload(is_empty=True)])
        resolver = SourceCraftRepositoryContextResolver(lambda: client, clock=lambda: ANALYZED_AT)

        context = await resolver.resolve(REPOSITORY_ID, bound_principal())

        self.assertEqual(context.commit_sha, "")
        self.assertEqual(client.calls, [REPOSITORY_BY_ID])
        self.assertTrue(client.closed)

    async def test_denied_repository_closes_client_and_does_not_continue(self) -> None:
        client = ScriptedClient(
            [SourceCraftAuthenticationError("denied", status_code=403)]
        )
        resolver = SourceCraftRepositoryContextResolver(lambda: client, clock=lambda: ANALYZED_AT)

        with self.assertRaises(PermissionError):
            await resolver.resolve(REPOSITORY_ID, bound_principal())

        self.assertEqual(client.calls, [REPOSITORY_BY_ID])
        self.assertTrue(client.closed)

    async def test_unknown_repository_is_lookup_error(self) -> None:
        client = ScriptedClient(
            [
                SourceCraftResponseError(
                    "SourceCraft returned unexpected HTTP 404",
                    status_code=404,
                )
            ]
        )
        resolver = SourceCraftRepositoryContextResolver(lambda: client, clock=lambda: ANALYZED_AT)

        with self.assertRaises(LookupError):
            await resolver.resolve(REPOSITORY_ID, bound_principal())

        self.assertTrue(client.closed)

    async def test_mismatched_repository_id_is_not_analyzed(self) -> None:
        client = ScriptedClient([repository_payload(repository_id="other-repo")])
        resolver = SourceCraftRepositoryContextResolver(lambda: client, clock=lambda: ANALYZED_AT)

        with self.assertRaises(LookupError):
            await resolver.resolve(REPOSITORY_ID, bound_principal())

        self.assertTrue(client.closed)

    async def test_rejected_token_is_unauthorized_and_closes_client(self) -> None:
        client = ScriptedClient(
            [SourceCraftAuthenticationError("rejected", status_code=401)]
        )
        resolver = SourceCraftRepositoryContextResolver(lambda: client, clock=lambda: ANALYZED_AT)

        with self.assertRaises(AnalysisLaunchError) as raised:
            await resolver.resolve(REPOSITORY_ID, bound_principal())

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Authentication required.")
        self.assertTrue(client.closed)
        self.assertEqual(client.calls, [REPOSITORY_BY_ID])

    async def test_rate_limit_does_not_create_context(self) -> None:
        client = ScriptedClient([SourceCraftRateLimitError(12)])
        resolver = SourceCraftRepositoryContextResolver(lambda: client, clock=lambda: ANALYZED_AT)

        with self.assertRaises(AnalysisLaunchError) as raised:
            await resolver.resolve(REPOSITORY_ID, bound_principal())

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.retry_after_seconds, 12)
        self.assertTrue(client.closed)

    async def test_timeout_is_unavailable(self) -> None:
        client = ScriptedClient([SourceCraftTimeoutError("SourceCraft request timed out")])
        resolver = SourceCraftRepositoryContextResolver(lambda: client, clock=lambda: ANALYZED_AT)

        with self.assertRaises(AnalysisLaunchError) as raised:
            await resolver.resolve(REPOSITORY_ID, bound_principal())

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "SourceCraft is unavailable.")
        self.assertTrue(client.closed)

    async def test_http_404_without_status_code_is_not_a_missing_repository(self) -> None:
        client = ScriptedClient(
            [SourceCraftResponseError("SourceCraft returned unexpected HTTP 404")]
        )
        resolver = SourceCraftRepositoryContextResolver(lambda: client, clock=lambda: ANALYZED_AT)

        with self.assertRaises(AnalysisLaunchError) as raised:
            await resolver.resolve(REPOSITORY_ID, bound_principal())

        self.assertEqual(raised.exception.status_code, 502)
        self.assertTrue(client.closed)

    async def test_missing_default_branch_is_unusable_snapshot(self) -> None:
        client = ScriptedClient(
            [
                repository_payload(),
                SourceCraftResponseError(
                    "SourceCraft returned unexpected HTTP 404",
                    status_code=404,
                ),
            ]
        )
        resolver = SourceCraftRepositoryContextResolver(lambda: client, clock=lambda: ANALYZED_AT)

        with self.assertRaises(AnalysisLaunchError) as raised:
            await resolver.resolve(REPOSITORY_ID, bound_principal())

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(client.calls, [REPOSITORY_BY_ID, BRANCHES_BY_ID])
        self.assertTrue(client.closed)

    async def test_mismatched_principal_does_not_call_sourcecraft(self) -> None:
        client = ScriptedClient([repository_payload()])
        _request_token.set(USER_TOKEN)
        resolver = SourceCraftRepositoryContextResolver(lambda: client, clock=lambda: ANALYZED_AT)

        with self.assertRaises(PermissionError):
            await resolver.resolve(REPOSITORY_ID, AnalysisPrincipal("token:someone-else"))

        self.assertEqual(client.calls, [])
        self.assertFalse(client.closed)

    async def test_opener_without_bound_token_does_not_call_sourcecraft(self) -> None:
        resolver = SourceCraftRepositoryContextResolver(
            open_request_sourcecraft_client,
            clock=lambda: ANALYZED_AT,
        )

        with self.assertRaises(PermissionError):
            await resolver.resolve(REPOSITORY_ID, AnalysisPrincipal("token:ignored"))

    def test_default_opener_closes_the_pool_it_created(self) -> None:
        _request_token.set(USER_TOKEN)
        client = open_request_sourcecraft_client()
        pool = client._http_client
        try:
            self.assertTrue(client._owns_http_client)
            self.assertEqual(current_request_token(), USER_TOKEN)
        finally:
            client.close()
            _request_token.set(None)
        self.assertTrue(pool.is_closed)

    async def test_principal_stores_digest_instead_of_token(self) -> None:
        request = Request(
            {
                "type": "http",
                "headers": [(b"authorization", f"Bearer {USER_TOKEN}".encode())],
            }
        )

        principal = await principal_from_authorization(request)

        digest = hashlib.sha256(USER_TOKEN.encode("utf-8")).hexdigest()
        self.assertEqual(principal.subject, f"token:{digest}")
        self.assertNotIn(USER_TOKEN, principal.subject)
        self.assertEqual(current_request_token(), USER_TOKEN)

    async def test_missing_bearer_is_rejected(self) -> None:
        request = Request({"type": "http", "headers": []})

        with self.assertRaises(PermissionError):
            await principal_from_authorization(request)


class ProjectLifeHttpLaunchTest(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_post_finishes_with_activity_and_issues(self) -> None:
        authorizations: list[str] = []
        pools: list[httpx.Client] = []
        ids = iter(("analysis-http-1", "analysis-http-2"))

        def handler(request: httpx.Request) -> httpx.Response:
            authorizations.append(request.headers["authorization"])
            return sourcecraft_response(request)

        def http_client_factory() -> httpx.Client:
            pool = httpx.Client(
                base_url="https://api.sourcecraft.tech",
                transport=httpx.MockTransport(handler),
            )
            pools.append(pool)
            return pool

        with patch.dict(
            os.environ,
            {"SOURCECRAFT_TOKEN": SERVICE_TOKEN, "DATABASE_URL": UNREACHABLE_DATABASE},
        ):
            application = isolated_sourcecraft_app(
                http_client_factory=http_client_factory,
                clock=lambda: ANALYZED_AT,
                analysis_id_factory=lambda: next(ids),
            )
            async with api_client(application) as client:
                created = await client.post(
                    f"/api/v1/repositories/{REPOSITORY_ID}/analyses",
                    headers={"Authorization": f"Bearer {USER_TOKEN}"},
                )
                completed = await _wait_for_terminal_status(client, "analysis-http-1")
                report = await client.get("/api/v1/analyses/analysis-http-1/report")
                self.assert_launch(created, completed, report, authorizations, USER_TOKEN)

                authorizations.clear()
                second = await client.post(
                    f"/api/v1/repositories/{REPOSITORY_ID}/analyses",
                    headers={"Authorization": f"Bearer {OTHER_TOKEN}"},
                )
                second_done = await _wait_for_terminal_status(client, "analysis-http-2")
                second_report = await client.get("/api/v1/analyses/analysis-http-2/report")
                self.assert_launch(second, second_done, second_report, authorizations, OTHER_TOKEN)

        # Два запуска: resolver и по клиенту на Activity и Issues. Все пулы закрыты.
        self.assertEqual(len(pools), 6)
        self.assertTrue(all(pool.is_closed for pool in pools))

    def assert_launch(
        self,
        created: httpx.Response,
        completed: httpx.Response,
        report: httpx.Response,
        authorizations: list[str],
        token: str,
    ) -> None:
        self.assertEqual(created.status_code, 202)
        self.assertEqual(created.json()["status"], "queued")
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "partial")
        self.assertEqual(report.status_code, 200)
        body = report.json()
        self.assertEqual(body["analysis"]["status"], "partial")
        self.assertEqual(body["analysis"]["commitSha"], COMMIT_SHA)
        categories = {item["code"]: item for item in body["categories"]}
        self.assertEqual(categories["activity"]["status"], "measured")
        self.assertEqual(categories["issues"]["status"], "measured")
        self.assertEqual(
            {item["code"] for item in categories["activity"]["evidence"]},
            {
                "last_activity_days",
                "merged_mr_in_period",
                "releases_in_period",
                "contributor_count",
            },
        )
        self.assertIn("stale_open_ratio", {item["code"] for item in categories["issues"]["evidence"]})
        self.assertIn(
            "median_days_to_close",
            {item["code"] for item in categories["issues"]["evidence"]},
        )
        for code in CATEGORY_WEIGHTS:
            if code not in {"activity", "issues"}:
                self.assertEqual(categories[code]["reason"], "analyzer_not_configured")
        rendered = report.text
        self.assertNotIn(SECRET_EMAIL, rendered)
        self.assertNotIn(token, rendered)
        self.assertNotIn(SERVICE_TOKEN, rendered)
        self.assertEqual(set(authorizations), {f"Bearer {token}"})
        self.assertNotIn(f"Bearer {SERVICE_TOKEN}", authorizations)

    async def test_missing_bearer_is_unauthorized_on_runtime_app(self) -> None:
        application = isolated_sourcecraft_app()

        async with api_client(application) as client:
            response = await client.post(f"/api/v1/repositories/{REPOSITORY_ID}/analyses")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Authentication required."})

    async def test_process_app_requires_bearer(self) -> None:
        self.assertIsNotNone(app.state.analysis_dispatcher)
        self.assertIsNotNone(app.state.principal_provider)

        async with api_client(app) as client:
            response = await client.post(f"/api/v1/repositories/{REPOSITORY_ID}/analyses")

        self.assertEqual(response.status_code, 401)

    async def test_sourcecraft_denial_does_not_start_analysis(self) -> None:
        authorizations: list[str] = []
        paths: list[str] = []
        created_ids: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            authorizations.append(request.headers["authorization"])
            paths.append(unquote(request.url.path))
            return httpx.Response(403)

        application = isolated_sourcecraft_app(
            http_client_factory=lambda: httpx.Client(
                base_url="https://api.sourcecraft.tech",
                transport=httpx.MockTransport(handler),
            ),
            clock=lambda: ANALYZED_AT,
            analysis_id_factory=lambda: created_ids.append("analysis-denied") or "analysis-denied",
        )

        async with api_client(application) as client:
            response = await client.post(
                f"/api/v1/repositories/{REPOSITORY_ID}/analyses",
                headers={"Authorization": f"Bearer {USER_TOKEN}"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Repository access denied."})
        self.assertEqual(created_ids, [])
        self.assertEqual(paths, [REPOSITORY_BY_ID])
        self.assertEqual(authorizations, [f"Bearer {USER_TOKEN}"])

    async def test_rejected_sourcecraft_token_is_unauthorized(self) -> None:
        response = await _post_with_status(401)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Authentication required."})

    async def test_rate_limit_is_too_many_requests(self) -> None:
        response = await _post_with_status(429, headers={"Retry-After": "9"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json(), {"detail": "SourceCraft rate limit exceeded."})
        self.assertEqual(response.headers["retry-after"], "9")

    async def test_upstream_failure_is_bad_gateway(self) -> None:
        response = await _post_with_status(500)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"detail": "SourceCraft returned an unusable response."})

    async def test_create_app_without_dispatcher_stays_unavailable(self) -> None:
        application = create_app(
            analysis_store=InMemoryAnalysisStore(),
            job_store=InMemoryAnalysisJobStore(),
        )

        async with api_client(application) as client:
            response = await client.post(f"/api/v1/repositories/{REPOSITORY_ID}/analyses")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Analysis dispatch is not configured."})


def sourcecraft_response(request: httpx.Request) -> httpx.Response:
    path = unquote(request.url.path)
    status_filter = request.url.params.get("filter")
    if path == REPOSITORY_BY_ID:
        return httpx.Response(200, json=repository_payload())
    if path == BRANCHES_BY_ID:
        return httpx.Response(
            200,
            json={"branches": [branch("main", COMMIT_SHA)], "next_page_token": ""},
        )
    if path == f"{ANALYZER_BASE}/issues":
        if status_filter == "status=open":
            issues = [issue("open-1", created_days_ago=10, updated_days_ago=1, status_type="initial")]
        elif status_filter == "status=in_progress":
            issues = []
        elif status_filter == "status=closed":
            issues = [
                issue(
                    f"closed-{index}",
                    created_days_ago=20,
                    updated_days_ago=5,
                    status_type="completed",
                )
                for index in range(3)
            ]
        else:
            raise AssertionError(status_filter)
        return httpx.Response(200, json={"issues": issues, "next_page_token": ""})
    if path == f"{ANALYZER_BASE}/contributors":
        return httpx.Response(
            200,
            json={"contributors": [{"id": "u1", "username": "alice"}], "next_page_token": ""},
        )
    if path == f"{ANALYZER_BASE}/pulls":
        return httpx.Response(
            200,
            json={
                "pull_requests": [
                    {
                        "slug": "1",
                        "title": "MR",
                        "status": "merged",
                        "created_at": moment(8),
                        "updated_at": moment(2),
                    }
                ],
                "next_page_token": "",
            },
        )
    if path == f"{ANALYZER_BASE}/releases":
        return httpx.Response(
            200,
            json={
                "releases": [
                    {
                        "tag": "v1",
                        "title": "v1",
                        "status": "published",
                        "released_at": moment(3),
                    }
                ],
                "next_page_token": "",
            },
        )
    if path == ANALYZER_BASE:
        return httpx.Response(200, json={"last_updated": moment(2), "is_empty": False})
    raise AssertionError(path)


async def _post_with_status(
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    created_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status_code, headers=headers)

    application = isolated_sourcecraft_app(
        http_client_factory=lambda: httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        ),
        clock=lambda: ANALYZED_AT,
        analysis_id_factory=lambda: created_ids.append("analysis-error") or "analysis-error",
    )
    async with api_client(application) as client:
        response = await client.post(
            f"/api/v1/repositories/{REPOSITORY_ID}/analyses",
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
    assert created_ids == []
    return response


def api_client(application: httpx.ASGITransport | object) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    )


async def _wait_for_terminal_status(
    client: httpx.AsyncClient,
    analysis_id: str,
) -> httpx.Response:
    for _ in range(100):
        response = await client.get(f"/api/v1/analyses/{analysis_id}")
        if response.json()["status"] in {"completed", "partial", "failed"}:
            return response
        await asyncio.sleep(0)
    raise AssertionError("Background analysis did not finish.")
