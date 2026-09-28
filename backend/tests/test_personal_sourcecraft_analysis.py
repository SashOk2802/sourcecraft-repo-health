from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

import httpx
from cryptography.fernet import Fernet
from fastapi import Request

from backend.app.analysis import (
    AnalysisExecutionService,
    AnalysisJob,
    AnalysisJobStatus,
    AnalysisPlan,
    AnalysisPrincipal,
    AnalyzerRegistration,
    InMemoryAnalysisJobStore,
    InMemoryAnalysisStore,
    InProcessAnalysisDispatcher,
)
from backend.app.analysis.personal_sourcecraft import PersonalOrPublicAnalysisPlanner
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef
from backend.app.identity import (
    InMemorySourceCraftConnectionStore,
    SourceCraftConnectionService,
    SourceCraftTokenVault,
)
from backend.app.integrations.sourcecraft import SourceCraftClient
from backend.app.main import create_app

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
COMMIT_SHA = "a" * 40
REPOSITORY_ID = "repo-private"
OWNER = "user-owner"
OTHER_USER = "user-other"
OWNER_PAT = "test-personal-pat-owner"


class PersonalSourceCraftAnalysisTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.snapshot_store = InMemoryAnalysisStore()
        self.job_store = InMemoryAnalysisJobStore()
        self.connection_store = InMemorySourceCraftConnectionStore()
        self.client_factory = SourceCraftApiClientFactory()
        self.connections = SourceCraftConnectionService(
            SourceCraftTokenVault(Fernet.generate_key().decode("ascii")),
            self.connection_store,
            sourcecraft_client_factory=self.client_factory,
            clock=lambda: NOW,
        )
        await self.connections.connect(OWNER, OWNER_PAT)
        self.planner = PersonalOrPublicAnalysisPlanner(
            connection_service=self.connections,
            public_resolver=None,
            public_analyzer_provider=lambda _: (),
            clock=lambda: NOW,
        )
        self.dispatcher = InProcessAnalysisDispatcher(
            execution_service=AnalysisExecutionService(
                job_store=self.job_store,
                snapshot_store=self.snapshot_store,
                clock=lambda: NOW,
            ),
            analysis_planner=self.planner,
            analysis_id_factory=lambda: "analysis-personal",
        )
        self.app = create_app(
            analysis_store=self.snapshot_store,
            job_store=self.job_store,
            analysis_dispatcher=self.dispatcher,
            principal_provider=session_principal,
            sourcecraft_connection_service=self.connections,
            configure_public_repository_catalog=False,
        )

    async def asyncTearDown(self) -> None:
        await self.dispatcher.close()
        self.client_factory.close()

    async def test_owner_runs_private_analysis_without_pat_leaks(self) -> None:
        lease = await self.connections.issue_lease(OWNER)
        assert lease is not None
        plan = await self.planner.plan(REPOSITORY_ID, AnalysisPrincipal(OWNER))

        async with api_client(self.app, OWNER) as client:
            created = await client.post(f"/api/v1/repositories/{REPOSITORY_ID}/analyses")
            completed = await wait_for_terminal_status(client, "analysis-personal")
            report = await client.get("/api/v1/analyses/analysis-personal/report")

            client.headers["authorization"] = f"Bearer {OTHER_USER}"
            other_status = await client.get("/api/v1/analyses/analysis-personal")
            other_report = await client.get("/api/v1/analyses/analysis-personal/report")

        job = await self.job_store.get("analysis-personal")
        assert job is not None
        body = report.json()
        categories = {item["code"]: item for item in body["categories"]}

        self.assertEqual(created.status_code, 202)
        self.assertEqual(completed.json()["status"], "partial")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(categories["activity"]["status"], "measured")
        self.assertEqual(categories["issues"]["status"], "measured")
        self.assertEqual(categories["cicd"]["status"], "measured")
        self.assertEqual(categories["documentation"]["status"], "unavailable")
        self.assertEqual(
            categories["documentation"]["reason"],
            "personal_git_transport_unavailable",
        )
        self.assertEqual(categories["code_health"]["status"], "unavailable")
        self.assertEqual(categories["security"]["status"], "unavailable")
        self.assertEqual(other_status.status_code, 404)
        self.assertEqual(other_report.status_code, 404)

        secret_surfaces = (
            repr(lease),
            repr(plan),
            repr(plan.context),
            repr(job),
            created.text,
            completed.text,
            report.text,
            other_status.text,
            other_report.text,
        )
        self.assertTrue(all(OWNER_PAT not in value for value in secret_surfaces))
        self.assertEqual(job.owner_subject, OWNER)
        self.assertNotIn(OWNER_PAT, job.owner_subject)

        api_authorizations = {
            authorization
            for path, authorization in self.client_factory.requests
            if path != "/user"
        }
        self.assertEqual(api_authorizations, {f"Bearer {OWNER_PAT}"})
        self.assertIn(
            "/repos/sample-org/sample-private/cicd/runs",
            {path for path, _ in self.client_factory.requests},
        )

    async def test_other_user_cannot_use_owner_connection_or_repository(self) -> None:
        before = len(self.client_factory.requests)
        lease = await self.connections.issue_lease(OWNER)
        assert lease is not None
        with self.assertRaises(PermissionError):
            self.connections.open_client(lease, OTHER_USER)

        async with api_client(self.app, OTHER_USER) as client:
            response = await client.post(f"/api/v1/repositories/{REPOSITORY_ID}/analyses")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"detail": "Connect SourceCraft to analyze this repository."},
        )
        self.assertEqual(len(self.client_factory.requests), before)
        self.assertNotIn(OWNER_PAT, response.text)

    async def test_revoked_pat_does_not_create_successful_job(self) -> None:
        self.client_factory.revoked_tokens.add(OWNER_PAT)

        async with api_client(self.app, OWNER) as client:
            response = await client.post(f"/api/v1/repositories/{REPOSITORY_ID}/analyses")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "SourceCraft rejected the token."})
        self.assertIsNone(await self.job_store.get("analysis-personal"))
        self.assertNotIn(OWNER_PAT, response.text)

    async def test_corrupt_encrypted_pat_returns_safe_503(self) -> None:
        await self.connection_store.upsert(OWNER, b"v1:corrupt", "owner", NOW)

        async with api_client(self.app, OWNER) as client:
            response = await client.post(f"/api/v1/repositories/{REPOSITORY_ID}/analyses")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "SourceCraft connection is unavailable."},
        )
        self.assertIsNone(await self.job_store.get("analysis-personal"))
        self.assertNotIn(OWNER_PAT, response.text)


class PersonalAnalysisFallbackAndRestartTest(unittest.IsolatedAsyncioTestCase):
    async def test_user_without_connection_uses_allowed_public_fallback(self) -> None:
        snapshot_store = InMemoryAnalysisStore()
        job_store = InMemoryAnalysisJobStore()
        resolver = PublicResolver()
        planner = PersonalOrPublicAnalysisPlanner(
            connection_service=None,
            public_resolver=resolver,
            public_analyzer_provider=lambda _: (measured_activity(),),
            clock=lambda: NOW,
        )
        dispatcher = InProcessAnalysisDispatcher(
            execution_service=AnalysisExecutionService(
                job_store=job_store,
                snapshot_store=snapshot_store,
                clock=lambda: NOW,
            ),
            analysis_planner=planner,
            analysis_id_factory=lambda: "analysis-public",
        )
        app = create_app(
            analysis_store=snapshot_store,
            job_store=job_store,
            analysis_dispatcher=dispatcher,
            principal_provider=session_principal,
            configure_public_repository_catalog=False,
        )

        try:
            async with api_client(app, OTHER_USER) as client:
                created = await client.post("/api/v1/repositories/repo-public/analyses")
                completed = await wait_for_terminal_status(client, "analysis-public")
        finally:
            await dispatcher.close()

        self.assertEqual(created.status_code, 202)
        self.assertEqual(completed.json()["status"], "partial")
        self.assertEqual(resolver.principals, [AnalysisPrincipal(OTHER_USER)])

    async def test_restart_fails_personal_job_without_reopening_credential(self) -> None:
        job_store = InMemoryAnalysisJobStore()
        old_job = AnalysisJob.queued(
            analysis_id="analysis-abandoned-personal",
            repository_id=REPOSITORY_ID,
            created_at=NOW - timedelta(minutes=2),
            owner_subject=OWNER,
            worker_id="worker-before-restart",
        )
        await job_store.create(old_job)
        planner = FailingIfCalledPlanner()
        dispatcher = InProcessAnalysisDispatcher(
            execution_service=AnalysisExecutionService(
                job_store=job_store,
                snapshot_store=InMemoryAnalysisStore(),
                clock=lambda: NOW,
                worker_lease_timeout=timedelta(seconds=30),
            ),
            analysis_planner=planner,
            worker_id="worker-after-restart",
        )

        try:
            await dispatcher.start()
        finally:
            await dispatcher.close()

        recovered = await job_store.get(old_job.analysis_id)
        assert recovered is not None
        self.assertEqual(recovered.status, AnalysisJobStatus.FAILED)
        self.assertEqual(recovered.error_code, "worker_interrupted")
        self.assertFalse(planner.called)


class SourceCraftApiClientFactory:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.revoked_tokens: set[str] = set()
        self.clients: list[OwnedHttpSourceCraftClient] = []

    def __call__(self, token: str) -> SourceCraftClient:
        def handle(request: httpx.Request) -> httpx.Response:
            authorization = request.headers.get("authorization", "")
            self.requests.append((request.url.path, authorization))
            if token in self.revoked_tokens:
                return httpx.Response(401, json={"error": "denied"})
            return sourcecraft_response(request)

        client = OwnedHttpSourceCraftClient(token, handle)
        self.clients.append(client)
        return client

    def close(self) -> None:
        for client in self.clients:
            client.close()


class OwnedHttpSourceCraftClient(SourceCraftClient):
    def __init__(self, token: str, handler) -> None:
        self._owned_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        )
        super().__init__(token, http_client=self._owned_client)

    def close(self) -> None:
        self._owned_client.close()


def sourcecraft_response(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/user":
        return httpx.Response(200, json={"username": "owner"})
    if path == f"/repos/id:{REPOSITORY_ID}":
        return httpx.Response(200, json=repository_payload())
    if path == f"/repos/id:{REPOSITORY_ID}/branches":
        return httpx.Response(
            200,
            json={
                "branches": [{"name": "main", "commit": {"hash": COMMIT_SHA}}],
                "next_page_token": "",
            },
        )
    if path == "/repos/sample-org/sample-private":
        return httpx.Response(200, json=repository_payload())
    if path.endswith("/contributors"):
        return paginated("contributors", [{"id": "contributor-1", "username": "dev"}])
    if path.endswith("/pulls"):
        return paginated(
            "pull_requests",
            [
                {
                    "slug": "1",
                    "title": "Example change",
                    "status": "merged",
                    "created_at": iso_days_ago(10),
                    "updated_at": iso_days_ago(8),
                }
            ],
        )
    if path.endswith("/releases"):
        return paginated(
            "releases",
            [{"tag": "v1.0.0", "released_at": iso_days_ago(5), "status": "released"}],
        )
    if path.endswith("/issues"):
        status = request.url.params.get("filter", "").removeprefix("status=")
        if status == "closed":
            items = [issue_payload(f"closed-{index}", 20 + index, 10 + index, "completed") for index in range(3)]
        elif status == "in_progress":
            items = [issue_payload("working-1", 15, 2, "in_progress")]
        else:
            items = [issue_payload("open-1", 20, 3, "open")]
        return paginated("issues", items)
    if path.endswith("/cicd/runs"):
        runs = [ci_run(index) for index in range(1, 6)]
        return paginated("runs", runs)
    return httpx.Response(404, json={"error": "not found"})


def repository_payload() -> dict[str, object]:
    return {
        "id": REPOSITORY_ID,
        "slug": "sample-private",
        "visibility": "private",
        "web_url": "https://sourcecraft.dev/sample-org/sample-private",
        "default_branch": "main",
        "is_empty": False,
        "last_updated": iso_days_ago(1),
        "organization": {"id": "org-example", "slug": "sample-org"},
    }


def paginated(field: str, items: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(200, json={field: items, "next_page_token": ""})


def issue_payload(slug: str, created_days: int, updated_days: int, status: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "slug": slug,
        "title": "Example issue",
        "created_at": iso_days_ago(created_days),
        "updated_at": iso_days_ago(updated_days),
        "status": {"status_type": status},
    }
    if status == "completed":
        payload["completed_at"] = iso_days_ago(updated_days)
    return payload


def ci_run(index: int) -> dict[str, object]:
    created = NOW - timedelta(days=index)
    return {
        "id": "",
        "slug": str(index),
        "status": "success",
        "event_type": "push",
        "dates": {
            "created_at": created.isoformat(),
            "started_at": (created + timedelta(seconds=1)).isoformat(),
            "finished_at": (created + timedelta(minutes=1)).isoformat(),
            "updated_at": (created + timedelta(minutes=1)).isoformat(),
        },
        "workflows": [{"slug": "ci"}],
    }


def iso_days_ago(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat()


async def session_principal(request: Request) -> AnalysisPrincipal:
    authorization = request.headers.get("authorization", "")
    scheme, _, subject = authorization.partition(" ")
    if scheme.lower() != "bearer" or not subject:
        raise PermissionError("authentication required")
    return AnalysisPrincipal(subject)


class PublicResolver:
    def __init__(self) -> None:
        self.principals: list[AnalysisPrincipal] = []

    async def resolve(self, repository_id: str, principal: AnalysisPrincipal) -> AnalysisContext:
        if repository_id != "repo-public":
            raise LookupError(repository_id)
        self.principals.append(principal)
        return AnalysisContext(
            repository=RepositoryRef(repository_id, "sample-org", "sample-public"),
            commit_sha=COMMIT_SHA,
            analyzed_at=NOW,
            period_start=NOW - timedelta(days=90),
            period_end=NOW,
        )


class FailingIfCalledPlanner:
    def __init__(self) -> None:
        self.called = False

    async def plan(self, _: str, __: AnalysisPrincipal) -> AnalysisPlan:
        self.called = True
        raise AssertionError("restart must not recreate a personal plan")


def measured_activity() -> AnalyzerRegistration:
    return AnalyzerRegistration(
        "activity",
        lambda _: CategoryResult(
            category="activity",
            status=DataStatus.MEASURED,
            score=80,
            summary="Activity measured.",
        ),
    )


def api_client(app, subject: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {subject}"},
    )


async def wait_for_terminal_status(
    client: httpx.AsyncClient,
    analysis_id: str,
) -> httpx.Response:
    for _ in range(100):
        response = await client.get(f"/api/v1/analyses/{analysis_id}")
        if response.json()["status"] in {"completed", "partial", "failed"}:
            return response
        await asyncio.sleep(0)
    raise AssertionError("Background analysis did not finish.")
