from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.app.analysis import AnalysisJob, AnalysisJobStatus, PostgresAnalysisJobStore


@unittest.skipUnless(
    os.getenv("TEST_POSTGRES") == "1",
    "Для интеграционного теста PostgreSQL установите TEST_POSTGRES=1.",
)
class PostgresAnalysisJobStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.created_at = datetime(2026, 9, 19, 10, tzinfo=UTC)
        self.analysis_id = f"integration-{uuid4().hex}"
        self.store = PostgresAnalysisJobStore(os.environ["DATABASE_URL"])
        await self.store.start()

    async def asyncTearDown(self) -> None:
        pool = self.store._require_pool()
        await pool.execute(
            "DELETE FROM analysis_jobs WHERE analysis_id = $1",
            self.analysis_id,
        )
        await self.store.close()

    async def test_persists_a_complete_lifecycle(self) -> None:
        job = AnalysisJob.queued(
            analysis_id=self.analysis_id,
            repository_id="repo-42",
            created_at=self.created_at,
        )

        await self.store.create(job)
        await self.store.mark_running(
            self.analysis_id,
            self.created_at + timedelta(seconds=1),
        )
        partial = await self.store.finish(
            self.analysis_id,
            status=AnalysisJobStatus.PARTIAL,
            finished_at=self.created_at + timedelta(seconds=2),
        )

        restored = await self.store.get(self.analysis_id)
        self.assertEqual(partial.status, AnalysisJobStatus.PARTIAL)
        self.assertEqual(restored, partial)
