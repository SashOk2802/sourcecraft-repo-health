from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.app.analysis import (
    AnalysisExecutionService,
    AnalysisJob,
    AnalysisJobStatus,
    AnalyzerRegistration,
    PostgresAnalysisJobStore,
    PostgresAnalysisStore,
    run_analysis,
)
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef


@unittest.skipUnless(
    os.getenv("TEST_POSTGRES") == "1",
    "Для интеграционного теста PostgreSQL установите TEST_POSTGRES=1.",
)
class PostgresAnalysisJobStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.created_at = datetime(2026, 9, 19, 10, tzinfo=UTC)
        self.analysis_id = f"integration-{uuid4().hex}"
        self.worker_ids: set[str] = set()
        database_url = os.environ["DATABASE_URL"]
        self.job_store = PostgresAnalysisJobStore(database_url)
        self.snapshot_store = PostgresAnalysisStore(database_url)
        await self.job_store.start()
        await self.snapshot_store.start()

    async def asyncTearDown(self) -> None:
        pool = self.job_store._require_pool()
        await pool.execute(
            "DELETE FROM analysis_snapshots WHERE analysis_id = $1",
            self.analysis_id,
        )
        await pool.execute(
            "DELETE FROM analysis_jobs WHERE analysis_id = $1",
            self.analysis_id,
        )
        if self.worker_ids:
            await pool.execute(
                "DELETE FROM analysis_worker_leases WHERE worker_id = ANY($1::text[])",
                list(self.worker_ids),
            )
        await self.snapshot_store.close()
        await self.job_store.close()

    async def test_persists_a_complete_lifecycle(self) -> None:
        job = AnalysisJob.queued(
            analysis_id=self.analysis_id,
            repository_id="repo-42",
            created_at=self.created_at,
            owner_subject="user-42",
        )

        await self.job_store.create(job)
        await self.job_store.mark_running(
            self.analysis_id,
            self.created_at + timedelta(seconds=1),
        )
        partial = await self.job_store.finish(
            self.analysis_id,
            status=AnalysisJobStatus.PARTIAL,
            finished_at=self.created_at + timedelta(seconds=2),
        )

        restored = await self.job_store.get(self.analysis_id)
        self.assertEqual(partial.status, AnalysisJobStatus.PARTIAL)
        self.assertEqual(restored, partial)

    async def test_saves_snapshot_and_terminal_state_in_one_transaction(self) -> None:
        job = AnalysisJob.queued(
            analysis_id=self.analysis_id,
            repository_id="repo-42",
            created_at=self.created_at,
            owner_subject="user-42",
        )
        execution = run_analysis(_context(self.created_at), (activity_registration(),))

        await self.job_store.create(job)
        await self.job_store.mark_running(
            self.analysis_id,
            self.created_at + timedelta(seconds=1),
        )
        completed = await self.snapshot_store.save_and_finish(
            self.analysis_id,
            execution,
            status=AnalysisJobStatus.PARTIAL,
            finished_at=self.created_at + timedelta(seconds=2),
        )

        self.assertEqual(completed.status, AnalysisJobStatus.PARTIAL)
        self.assertEqual((await self.job_store.get(self.analysis_id)).status, completed.status)
        self.assertIsNotNone(await self.snapshot_store.get(self.analysis_id))

    async def test_does_not_finish_job_when_snapshot_insert_fails(self) -> None:
        job = AnalysisJob.queued(
            analysis_id=self.analysis_id,
            repository_id="repo-42",
            created_at=self.created_at,
            owner_subject="user-42",
        )
        execution = run_analysis(_context(self.created_at), (activity_registration(),))

        await self.job_store.create(job)
        await self.job_store.mark_running(
            self.analysis_id,
            self.created_at + timedelta(seconds=1),
        )
        await self.snapshot_store.save(self.analysis_id, execution)

        with self.assertRaisesRegex(ValueError, "analysis_id already exists"):
            await self.snapshot_store.save_and_finish(
                self.analysis_id,
                execution,
                status=AnalysisJobStatus.PARTIAL,
                finished_at=self.created_at + timedelta(seconds=2),
            )

        self.assertEqual(
            (await self.job_store.get(self.analysis_id)).status,
            AnalysisJobStatus.RUNNING,
        )

    async def test_second_store_does_not_finish_job_with_active_first_worker_lease(
        self,
    ) -> None:
        first_worker = f"worker-first-{uuid4().hex}"
        second_worker = f"worker-second-{uuid4().hex}"
        self.worker_ids.update((first_worker, second_worker))
        job = AnalysisJob.queued(
            analysis_id=self.analysis_id,
            repository_id="repo-42",
            created_at=self.created_at,
            owner_subject="user-42",
            worker_id=first_worker,
        )
        await self.job_store.heartbeat_worker(
            first_worker,
            self.created_at + timedelta(seconds=1),
        )
        await self.job_store.create(job)
        await self.job_store.mark_running(
            self.analysis_id,
            self.created_at + timedelta(seconds=1),
            worker_id=first_worker,
        )

        second_store = PostgresAnalysisJobStore(os.environ["DATABASE_URL"])
        await second_store.start()
        try:
            second_worker_service = AnalysisExecutionService(
                job_store=second_store,
                snapshot_store=self.snapshot_store,
                clock=lambda: self.created_at + timedelta(seconds=2),
            )
            recovered = await second_worker_service.start_worker(second_worker)
            restored = await second_store.get(self.analysis_id)
        finally:
            await second_store.close()

        self.assertEqual(recovered, ())
        self.assertEqual(restored.status, AnalysisJobStatus.RUNNING)
        self.assertEqual(restored.worker_id, first_worker)

    async def test_recovers_legacy_ownerless_job_after_migration_grace_period(
        self,
    ) -> None:
        job = AnalysisJob.queued(
            analysis_id=self.analysis_id,
            repository_id="repo-42",
            created_at=self.created_at,
            owner_subject="user-42",
        )
        await self.job_store.create(job)
        await self.job_store.mark_running(
            self.analysis_id,
            self.created_at + timedelta(seconds=1),
        )

        pool = self.job_store._require_pool()
        original_grace_deadline = await pool.fetchval(
            """
            SELECT ownerless_recovery_after
            FROM analysis_job_recovery_state
            WHERE id = 1
            """
        )
        try:
            await pool.execute(
                """
                UPDATE analysis_job_recovery_state
                SET ownerless_recovery_after = $1
                WHERE id = 1
                """,
                self.created_at + timedelta(seconds=3),
            )
            during_grace = await self.job_store.recover_abandoned(
                finished_at=self.created_at + timedelta(seconds=2),
                stale_before=self.created_at,
            )

            await pool.execute(
                """
                UPDATE analysis_job_recovery_state
                SET ownerless_recovery_after = $1
                WHERE id = 1
                """,
                self.created_at + timedelta(seconds=2),
            )
            recovered = await self.job_store.recover_abandoned(
                finished_at=self.created_at + timedelta(seconds=3),
                stale_before=self.created_at,
            )
        finally:
            await pool.execute(
                """
                UPDATE analysis_job_recovery_state
                SET ownerless_recovery_after = $1
                WHERE id = 1
                """,
                original_grace_deadline,
            )

        restored = await self.job_store.get(self.analysis_id)
        self.assertEqual(during_grace, ())
        self.assertEqual([item.analysis_id for item in recovered], [self.analysis_id])
        self.assertEqual(restored.status, AnalysisJobStatus.FAILED)
        self.assertEqual(restored.error_code, "worker_interrupted")


def _context(timestamp: datetime) -> AnalysisContext:
    return AnalysisContext(
        repository=RepositoryRef("repo-42", "team", "platform-api"),
        commit_sha="abc123",
        analyzed_at=timestamp,
        period_start=timestamp,
        period_end=timestamp,
    )


def activity_registration() -> AnalyzerRegistration:
    def evaluate(_: AnalysisContext) -> CategoryResult:
        return CategoryResult(
            category="activity",
            status=DataStatus.MEASURED,
            score=80,
            summary="Активность измерена.",
        )

    return AnalyzerRegistration("activity", evaluate)
