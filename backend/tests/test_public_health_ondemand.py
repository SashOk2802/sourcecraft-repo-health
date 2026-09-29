from __future__ import annotations

import asyncio
import hashlib
import unittest
from datetime import UTC, datetime, timedelta

import httpx

from backend.app.analysis.dispatch import AnalysisPrincipal
from backend.app.analysis.jobs import (
    AnalysisJob,
    AnalysisJobStatus,
    InMemoryAnalysisJobStore,
)
from backend.app.analysis.personal_sourcecraft import SourceCraftConnectionRequiredError
from backend.app.analysis.store import InMemoryAnalysisStore
from backend.app.integrations.sourcecraft_repositories import SourceCraftRepository
from backend.app.main import create_app
from backend.app.scheduling.runner import (
    SYSTEM_SCHEDULER_SUBJECT,
    InMemoryAnalysisScheduleStore,
    PublicAnalysisScheduler,
    ScheduleCandidate,
)


def _public_repo(
    repo_id: str = "repo-public",
    org: str = "demo-org",
    slug: str = "health-api",
) -> SourceCraftRepository:
    return SourceCraftRepository(
        id=repo_id,
        name=slug,
        organization_slug=org,
        slug=slug,
        default_branch="main",
        visibility="public",
        is_empty=False,
        language="Python",
        branch_count=1,
        web_url=f"https://sourcecraft.dev/{org}/{slug}",
    )


class _Catalog:
    def __init__(self, repositories: tuple[SourceCraftRepository, ...] = ()) -> None:
        self.repositories = repositories

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        return self.repositories


class _MockDispatcher:
    def __init__(
        self,
        job_store: InMemoryAnalysisJobStore | None = None,
        delay: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.submissions: list[str] = []
        self.job_store = job_store
        self.delay = delay
        self.error = error

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def submit(
        self,
        repository_id: str,
        principal: AnalysisPrincipal,
        *,
        analysis_id: str | None = None,
    ) -> AnalysisJob | None:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if analysis_id is not None:
            self.submissions.append(analysis_id)
            job = AnalysisJob.queued(
                analysis_id=analysis_id,
                repository_id=repository_id,
                owner_subject=principal.subject,
                created_at=datetime.now(UTC),
            )
            if self.job_store is not None:
                return await self.job_store.create(job)
            return job
        return None


def _api_client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://testserver",
    )


class PublicHealthOndemandTest(unittest.IsolatedAsyncioTestCase):
    async def test_ondemand_submission_and_rate_limit(self) -> None:
        store = InMemoryAnalysisStore()
        jobs = InMemoryAnalysisJobStore()
        dispatcher = _MockDispatcher(job_store=jobs)
        catalog = _Catalog((_public_repo(),))
        app = create_app(
            analysis_store=store,
            job_store=jobs,
            repository_catalog=catalog,
            analysis_dispatcher=dispatcher,
        )

        repo_hash = hashlib.sha256(b"repo-public").hexdigest()[:24]

        async with _api_client(app) as client:
            # 1. No jobs, should submit (initial)
            res1 = await client.get("/api/v1/public/repositories/demo-org/health-api/health")
            self.assertEqual(res1.status_code, 404)
            self.assertEqual(len(dispatcher.submissions), 1)
            self.assertEqual(dispatcher.submissions[0], f"ondemand-{repo_hash}-init")

            # 2. An active queued job already exists from step 1, should NOT submit
            res2 = await client.get("/api/v1/public/repositories/demo-org/health-api/health")
            self.assertEqual(res2.status_code, 404)
            self.assertEqual(len(dispatcher.submissions), 1)

            # 3. Job finished recently (cooldown NOT passed), should NOT submit
            now = datetime.now(UTC)
            for job in list(jobs._jobs.values()):
                jobs._jobs[job.analysis_id] = AnalysisJob(
                    analysis_id=job.analysis_id,
                    repository_id="repo-public",
                    owner_subject="public-api-ondemand",
                    status=AnalysisJobStatus.COMPLETED,
                    created_at=now - timedelta(minutes=10),
                    started_at=now - timedelta(minutes=5),
                    finished_at=now,
                )
            res3 = await client.get("/api/v1/public/repositories/demo-org/health-api/health")
            self.assertEqual(res3.status_code, 404)
            self.assertEqual(len(dispatcher.submissions), 1)

            # 4. Job finished long time ago, cooldown passed, should submit with timestamp-based suffix
            old_finish = datetime.now(UTC) - timedelta(hours=2)
            for job in list(jobs._jobs.values()):
                jobs._jobs[job.analysis_id] = AnalysisJob(
                    analysis_id=job.analysis_id,
                    repository_id="repo-public",
                    owner_subject="public-api-ondemand",
                    status=AnalysisJobStatus.COMPLETED,
                    created_at=old_finish - timedelta(minutes=10),
                    started_at=old_finish - timedelta(minutes=5),
                    finished_at=old_finish,
                )
            res4 = await client.get("/api/v1/public/repositories/demo-org/health-api/health")
            self.assertEqual(res4.status_code, 404)
            self.assertEqual(len(dispatcher.submissions), 2)
            self.assertEqual(
                dispatcher.submissions[1],
                f"ondemand-{repo_hash}-{int(old_finish.timestamp())}",
            )

    async def test_concurrent_requests_submit_only_once(self) -> None:
        store = InMemoryAnalysisStore()
        jobs = InMemoryAnalysisJobStore()
        dispatcher = _MockDispatcher(job_store=jobs, delay=0.05)
        catalog = _Catalog((_public_repo(),))
        app = create_app(
            analysis_store=store,
            job_store=jobs,
            repository_catalog=catalog,
            analysis_dispatcher=dispatcher,
        )

        async with _api_client(app) as client:
            res1, res2 = await asyncio.gather(
                client.get("/api/v1/public/repositories/demo-org/health-api/health"),
                client.get("/api/v1/public/repositories/demo-org/health-api/health"),
            )
            self.assertEqual(res1.status_code, 404)
            self.assertEqual(res2.status_code, 404)
            self.assertEqual(len(dispatcher.submissions), 1)

    async def test_unusual_repository_id_generates_url_safe_analysis_id(self) -> None:
        store = InMemoryAnalysisStore()
        jobs = InMemoryAnalysisJobStore()
        dispatcher = _MockDispatcher(job_store=jobs)
        catalog = _Catalog(
            (
                _public_repo(
                    repo_id="custom/complex:repo id#123",
                    org="demo-org",
                    slug="complex-api",
                ),
            )
        )
        app = create_app(
            analysis_store=store,
            job_store=jobs,
            repository_catalog=catalog,
            analysis_dispatcher=dispatcher,
        )

        async with _api_client(app) as client:
            res = await client.get("/api/v1/public/repositories/demo-org/complex-api/health")
            self.assertEqual(res.status_code, 404)
            self.assertEqual(len(dispatcher.submissions), 1)
            submitted_id = dispatcher.submissions[0]
            self.assertLessEqual(len(submitted_id), 128)
            self.assertTrue(all(c.isalnum() or c in "._~-" for c in submitted_id))

    async def test_missing_public_resolver_keeps_not_found_response(self) -> None:
        store = InMemoryAnalysisStore()
        jobs = InMemoryAnalysisJobStore()
        dispatcher = _MockDispatcher(error=SourceCraftConnectionRequiredError())
        app = create_app(
            analysis_store=store,
            job_store=jobs,
            repository_catalog=_Catalog((_public_repo(),)),
            analysis_dispatcher=dispatcher,
        )

        async with _api_client(app) as client:
            response = await client.get(
                "/api/v1/public/repositories/demo-org/health-api/health"
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Public health score not found."})

    async def test_active_scheduled_analysis_is_not_duplicated(self) -> None:
        store = InMemoryAnalysisStore()
        jobs = InMemoryAnalysisJobStore()
        dispatcher = _MockDispatcher(job_store=jobs)
        app = create_app(
            analysis_store=store,
            job_store=jobs,
            repository_catalog=_Catalog((_public_repo(),)),
            analysis_dispatcher=dispatcher,
        )
        await jobs.create(
            AnalysisJob.queued(
                analysis_id="scheduled-job",
                repository_id="repo-public",
                owner_subject=SYSTEM_SCHEDULER_SUBJECT,
                created_at=datetime.now(UTC),
            )
        )

        async with _api_client(app) as client:
            response = await client.get(
                "/api/v1/public/repositories/demo-org/health-api/health"
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(dispatcher.submissions, [])

    async def test_scheduler_reservation_blocks_ondemand_duplicate(self) -> None:
        now = datetime(2026, 9, 29, 20, tzinfo=UTC)
        store = InMemoryAnalysisStore()
        jobs = InMemoryAnalysisJobStore()
        dispatcher = _MockDispatcher(job_store=jobs)
        catalog = _Catalog((_public_repo(),))
        schedule_store = InMemoryAnalysisScheduleStore()
        await schedule_store.reconcile_catalog(
            (ScheduleCandidate("repo-public", None),),
            observed_at=now,
        )
        claimed = await schedule_store.claim_due(
            now=now,
            limit=1,
            lease_owner="periodic-scheduler",
            lease_expires_at=now + timedelta(minutes=5),
        )
        self.assertEqual(len(claimed), 1)
        self.assertTrue(
            await schedule_store.reserve_submission(
                "repo-public",
                lease_owner="periodic-scheduler",
                analysis_id="scheduled-reservation",
                updated_at=now,
            )
        )
        scheduler = PublicAnalysisScheduler(
            repository_catalog=catalog,
            dispatcher=dispatcher,
            job_store=jobs,
            schedule_store=schedule_store,
            clock=lambda: now,
        )
        app = create_app(
            analysis_store=store,
            job_store=jobs,
            repository_catalog=catalog,
            analysis_dispatcher=dispatcher,
            analysis_scheduler=scheduler,
        )

        async with _api_client(app) as client:
            response = await client.get(
                "/api/v1/public/repositories/demo-org/health-api/health"
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(dispatcher.submissions, [])

    async def test_ondemand_uses_scheduler_reservation(self) -> None:
        now = datetime(2026, 9, 29, 20, tzinfo=UTC)
        store = InMemoryAnalysisStore()
        jobs = InMemoryAnalysisJobStore()
        dispatcher = _MockDispatcher(job_store=jobs)
        catalog = _Catalog((_public_repo(),))
        schedule_store = InMemoryAnalysisScheduleStore()
        await schedule_store.reconcile_catalog(
            (ScheduleCandidate("repo-public", None),),
            observed_at=now,
        )
        scheduler = PublicAnalysisScheduler(
            repository_catalog=catalog,
            dispatcher=dispatcher,
            job_store=jobs,
            schedule_store=schedule_store,
            clock=lambda: now,
            analysis_id_factory=lambda: "ondemand-scheduled-job",
        )
        app = create_app(
            analysis_store=store,
            job_store=jobs,
            repository_catalog=catalog,
            analysis_dispatcher=dispatcher,
            analysis_scheduler=scheduler,
        )

        async with _api_client(app) as client:
            response = await client.get(
                "/api/v1/public/repositories/demo-org/health-api/health"
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(dispatcher.submissions, ["ondemand-scheduled-job"])
        self.assertEqual(
            (await jobs.get("ondemand-scheduled-job")).owner_subject,
            SYSTEM_SCHEDULER_SUBJECT,
        )

    async def test_scheduler_retry_backoff_blocks_ondemand_submission(self) -> None:
        now = datetime(2026, 9, 29, 20, tzinfo=UTC)
        store = InMemoryAnalysisStore()
        jobs = InMemoryAnalysisJobStore()
        dispatcher = _MockDispatcher(job_store=jobs)
        catalog = _Catalog((_public_repo(),))
        schedule_store = InMemoryAnalysisScheduleStore()
        await schedule_store.reconcile_catalog(
            (ScheduleCandidate("repo-public", None),),
            observed_at=now,
        )
        claimed = await schedule_store.claim_due(
            now=now,
            limit=1,
            lease_owner="periodic-scheduler",
            lease_expires_at=now + timedelta(minutes=5),
        )
        self.assertEqual(len(claimed), 1)
        self.assertTrue(
            await schedule_store.reserve_submission(
                "repo-public",
                lease_owner="periodic-scheduler",
                analysis_id="failed-scheduled-job",
                updated_at=now,
            )
        )
        self.assertTrue(
            await schedule_store.release_submission(
                "repo-public",
                lease_owner="periodic-scheduler",
                analysis_id="failed-scheduled-job",
                next_analysis_at=now + timedelta(minutes=15),
                consecutive_failures=1,
                updated_at=now,
            )
        )
        scheduler = PublicAnalysisScheduler(
            repository_catalog=catalog,
            dispatcher=dispatcher,
            job_store=jobs,
            schedule_store=schedule_store,
            clock=lambda: now,
        )
        app = create_app(
            analysis_store=store,
            job_store=jobs,
            repository_catalog=catalog,
            analysis_dispatcher=dispatcher,
            analysis_scheduler=scheduler,
        )

        async with _api_client(app) as client:
            response = await client.get(
                "/api/v1/public/repositories/demo-org/health-api/health"
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(dispatcher.submissions, [])


