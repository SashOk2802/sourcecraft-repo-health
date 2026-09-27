from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from backend.app.analysis.dispatch import AnalysisPrincipal
from backend.app.analysis.jobs import AnalysisJob, AnalysisJobStatus
from backend.app.scheduling.runner import (
    SYSTEM_SCHEDULER_SUBJECT,
    InMemoryAnalysisScheduleStore,
    PublicAnalysisScheduler,
    ScheduleCandidate,
)


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class _Repository:
    def __init__(self, identifier: str, last_activity_at: datetime | None) -> None:
        self.id = identifier
        self.last_activity_at = last_activity_at


class _Catalog:
    def __init__(self, repositories: tuple[_Repository, ...]) -> None:
        self.repositories = repositories
        self.error: Exception | None = None

    async def list_repositories(self) -> tuple[_Repository, ...]:
        if self.error is not None:
            raise self.error
        return self.repositories


class _Dispatcher:
    def __init__(self, clock: _Clock) -> None:
        self._clock = clock
        self.requests: list[tuple[str, AnalysisPrincipal]] = []
        self.jobs: dict[str, AnalysisJob] = {}
        self.submission_error: Exception | None = None
        self.first_submit_started: asyncio.Event | None = None
        self.allow_first_submission: asyncio.Event | None = None

    async def submit(
        self,
        repository_id: str,
        principal: AnalysisPrincipal,
        *,
        analysis_id: str | None = None,
    ) -> AnalysisJob:
        self.requests.append((repository_id, principal))
        if self.submission_error is not None:
            raise self.submission_error
        if len(self.requests) == 1 and self.first_submit_started is not None:
            self.first_submit_started.set()
            if self.allow_first_submission is not None:
                await self.allow_first_submission.wait()
        identifier = analysis_id or f"scheduled-{len(self.requests)}"
        existing = self.jobs.get(identifier)
        if existing is not None:
            return existing
        job = AnalysisJob.queued(
            analysis_id=identifier,
            repository_id=repository_id,
            created_at=self._clock(),
            owner_subject=principal.subject,
            worker_id="worker-test",
        )
        self.jobs[identifier] = job
        return job


class _JobStore:
    def __init__(self, dispatcher: _Dispatcher) -> None:
        self._dispatcher = dispatcher

    async def get(self, analysis_id: str) -> AnalysisJob | None:
        return self._dispatcher.jobs.get(analysis_id)


class PublicAnalysisSchedulerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 27, 12, tzinfo=UTC)
        self.clock = _Clock(self.now)
        self.dispatcher = _Dispatcher(self.clock)
        self.jobs = _JobStore(self.dispatcher)
        self.store = InMemoryAnalysisScheduleStore()

    def _scheduler(
        self,
        repositories: tuple[_Repository, ...],
        *,
        batch_size: int = 10,
        scheduler_id: str = "scheduler-test",
        catalog: _Catalog | None = None,
    ) -> PublicAnalysisScheduler:
        return PublicAnalysisScheduler(
            repository_catalog=catalog or _Catalog(repositories),
            dispatcher=self.dispatcher,
            job_store=self.jobs,
            schedule_store=self.store,
            clock=self.clock,
            batch_size=batch_size,
            scheduler_id=scheduler_id,
            analysis_id_factory=lambda: f"scheduled-{len(self.dispatcher.requests) + 1}",
        )

    async def test_starts_only_bounded_public_jobs_and_does_not_repeat_in_flight(self) -> None:
        scheduler = self._scheduler(
            (
                _Repository("repo-a", self.now),
                _Repository("repo-b", self.now),
            ),
            batch_size=1,
        )

        first = await scheduler.run_once()
        second = await scheduler.run_once()
        third = await scheduler.run_once()

        self.assertEqual((first.submitted, second.submitted, third.submitted), (1, 1, 0))
        self.assertEqual(
            [principal.subject for _, principal in self.dispatcher.requests],
            [SYSTEM_SCHEDULER_SUBJECT, SYSTEM_SCHEDULER_SUBJECT],
        )
        self.assertEqual(
            [repository_id for repository_id, _ in self.dispatcher.requests],
            ["repo-a", "repo-b"],
        )

    async def test_completed_job_receives_regular_next_run(self) -> None:
        scheduler = self._scheduler((_Repository("repo-a", self.now),))
        await scheduler.run_once()
        job = self.dispatcher.jobs["scheduled-1"]
        finished_at = self.now + timedelta(minutes=1)
        self.dispatcher.jobs[job.analysis_id] = job.started(self.now).finished(
            status=AnalysisJobStatus.COMPLETED,
            finished_at=finished_at,
        )
        self.clock.value = finished_at

        result = await scheduler.run_once()

        self.assertEqual((result.reconciled, result.submitted), (1, 0))
        self.clock.value = finished_at + timedelta(hours=5)
        self.assertEqual((await scheduler.run_once()).submitted, 0)

    async def test_expired_reservation_reuses_the_same_analysis_id(self) -> None:
        repositories = (_Repository("repo-a", self.now),)
        first = self._scheduler(repositories, scheduler_id="scheduler-first")
        second = self._scheduler(repositories, scheduler_id="scheduler-second")
        self.dispatcher.first_submit_started = asyncio.Event()
        self.dispatcher.allow_first_submission = asyncio.Event()

        first_run = asyncio.create_task(first.run_once())
        await self.dispatcher.first_submit_started.wait()
        self.clock.value = self.now + timedelta(minutes=6)

        second_result = await second.run_once()
        self.dispatcher.allow_first_submission.set()
        first_result = await first_run

        self.assertEqual((first_result.submitted, second_result.submitted), (1, 1))
        self.assertEqual(set(self.dispatcher.jobs), {"scheduled-1"})
        self.assertEqual(len(await self.store.list_in_flight()), 1)

    async def test_permanent_catalog_error_blocks_periodic_requests_until_restart(
        self,
    ) -> None:
        catalog = _Catalog((_Repository("repo-a", self.now),))
        catalog.error = PermissionError("SourceCraft catalog access is denied")
        scheduler = self._scheduler((), catalog=catalog)

        failed = await scheduler.run_once()
        catalog.error = None
        repeated = await scheduler.run_once()

        self.assertEqual((failed.deferred, failed.blocked), (0, 1))
        self.assertEqual((repeated.deferred, repeated.blocked), (0, 0))
        self.assertEqual(self.dispatcher.requests, [])

    async def test_temporary_catalog_error_uses_backoff_before_retrying(self) -> None:
        catalog = _Catalog((_Repository("repo-a", self.now),))
        catalog.error = TimeoutError("SourceCraft catalog timed out")
        scheduler = self._scheduler((), catalog=catalog)

        failed = await scheduler.run_once()
        repeated = await scheduler.run_once()

        self.assertEqual((failed.deferred, failed.blocked), (1, 0))
        self.assertEqual((repeated.deferred, repeated.blocked), (0, 0))
        self.assertEqual(self.dispatcher.requests, [])

    async def test_permanent_submission_error_blocks_repository_until_operator_action(
        self,
    ) -> None:
        scheduler = self._scheduler((_Repository("repo-a", self.now),))
        self.dispatcher.submission_error = PermissionError("SourceCraft access is denied")

        failed = await scheduler.run_once()
        self.dispatcher.submission_error = None
        repeated = await scheduler.run_once()

        self.assertEqual(
            (failed.submitted, failed.deferred, failed.blocked),
            (0, 0, 1),
        )
        self.assertEqual((repeated.submitted, repeated.deferred, repeated.blocked), (0, 0, 0))
        self.assertEqual(len(self.dispatcher.requests), 1)
        entry = self.store._entries["repo-a"]
        self.assertTrue(entry.blocked)
        self.assertIsNone(entry.in_flight_analysis_id)

    async def test_timeout_releases_repository_for_limited_retry(self) -> None:
        scheduler = self._scheduler((_Repository("repo-a", self.now),))
        self.dispatcher.submission_error = TimeoutError("SourceCraft timed out")

        failed = await scheduler.run_once()
        repeated = await scheduler.run_once()

        self.assertEqual(
            (failed.submitted, failed.deferred, failed.blocked),
            (0, 1, 0),
        )
        self.assertEqual((repeated.submitted, repeated.deferred, repeated.blocked), (0, 0, 0))
        self.assertEqual(len(self.dispatcher.requests), 1)
        self.assertFalse(self.store._entries["repo-a"].blocked)

    async def test_failed_job_uses_retry_delay_before_a_new_submission(self) -> None:
        scheduler = self._scheduler((_Repository("repo-a", self.now),))
        await scheduler.run_once()
        job = self.dispatcher.jobs["scheduled-1"]
        finished_at = self.now + timedelta(minutes=1)
        self.dispatcher.jobs[job.analysis_id] = job.started(self.now).finished(
            status=AnalysisJobStatus.FAILED,
            finished_at=finished_at,
            error_code="analysis_execution_failed",
            error_summary="Безопасная ошибка.",
        )
        self.clock.value = finished_at

        result = await scheduler.run_once()

        self.assertEqual((result.reconciled, result.submitted), (1, 0))
        self.clock.value = finished_at + timedelta(minutes=14)
        self.assertEqual((await scheduler.run_once()).submitted, 0)
        self.clock.value = finished_at + timedelta(minutes=17)
        self.assertEqual((await scheduler.run_once()).submitted, 1)


class InMemoryAnalysisScheduleStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_lease_prevents_second_scheduler_and_release_makes_entry_due_later(self) -> None:
        now = datetime(2026, 9, 27, 12, tzinfo=UTC)
        store = InMemoryAnalysisScheduleStore()
        await store.reconcile_catalog(
            (ScheduleCandidate("repo-a", now),),
            observed_at=now,
        )

        first = await store.claim_due(
            now=now,
            limit=1,
            lease_owner="scheduler-a",
            lease_expires_at=now + timedelta(minutes=1),
        )
        second = await store.claim_due(
            now=now,
            limit=1,
            lease_owner="scheduler-b",
            lease_expires_at=now + timedelta(minutes=1),
        )
        reserved = await store.reserve_submission(
            "repo-a",
            lease_owner="scheduler-a",
            analysis_id="analysis-reserved",
            updated_at=now,
        )
        released = await store.release_submission(
            "repo-a",
            lease_owner="scheduler-a",
            analysis_id="analysis-reserved",
            next_analysis_at=now + timedelta(minutes=15),
            consecutive_failures=1,
            updated_at=now,
        )
        after_release = await store.claim_due(
            now=now + timedelta(minutes=1),
            limit=1,
            lease_owner="scheduler-b",
            lease_expires_at=now + timedelta(minutes=2),
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(second, ())
        self.assertTrue(reserved)
        self.assertTrue(released)
        self.assertEqual(after_release, ())
