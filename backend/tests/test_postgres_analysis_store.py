from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from uuid import uuid4

from backend.app.analysis import PostgresAnalysisStore, run_analysis
from backend.app.contracts import AnalysisContext, RepositoryRef


@unittest.skipUnless(
    os.getenv("TEST_POSTGRES") == "1",
    "Для интеграционного теста PostgreSQL установите TEST_POSTGRES=1.",
)
class PostgresAnalysisStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        timestamp = datetime(2026, 9, 18, tzinfo=UTC)
        context = AnalysisContext(
            repository=RepositoryRef("repo-42", "team", "platform-api"),
            commit_sha="abc123",
            analyzed_at=timestamp,
            period_start=timestamp,
            period_end=timestamp,
        )
        self.execution = run_analysis(context, ())
        self.analysis_id = f"integration-{uuid4().hex}"
        self.store = PostgresAnalysisStore(os.environ["DATABASE_URL"])
        await self.store.start()

    async def asyncTearDown(self) -> None:
        pool = self.store._require_pool()
        await pool.execute(
            "DELETE FROM analysis_snapshots WHERE analysis_id = $1",
            self.analysis_id,
        )
        await self.store.close()

    async def test_persists_and_reads_a_snapshot(self) -> None:
        await self.store.save(self.analysis_id, self.execution)

        snapshot = await self.store.get(self.analysis_id)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.report["analysis"]["id"], self.analysis_id)
        expected_heading = f"Анализ {chr(96)}{self.analysis_id}{chr(96)}"
        self.assertIn(expected_heading, snapshot.markdown)
