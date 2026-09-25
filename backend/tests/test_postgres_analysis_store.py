from __future__ import annotations

import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.app.analysis import PostgresAnalysisStore, run_analysis
from backend.app.contracts import AnalysisContext, RepositoryRef


@unittest.skipUnless(
    os.getenv("TEST_POSTGRES") == "1",
    "Для интеграционного теста PostgreSQL установите TEST_POSTGRES=1.",
)
class PostgresAnalysisStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.timestamp = datetime(2026, 9, 18, tzinfo=UTC)
        self.execution = self._execution("repo-42", self.timestamp)
        self.analysis_ids = [f"integration-{uuid4().hex}"]
        self.store = PostgresAnalysisStore(os.environ["DATABASE_URL"])
        await self.store.start()

    def _execution(
        self,
        repository_id: str,
        analyzed_at: datetime,
        *,
        methodology_version: str = "v1",
    ):
        context = AnalysisContext(
            repository=RepositoryRef(repository_id, "team", f"repository-{repository_id}"),
            commit_sha="abc123",
            analyzed_at=analyzed_at,
            period_start=analyzed_at,
            period_end=analyzed_at,
        )
        execution = run_analysis(context, ())
        return replace(
            execution,
            analysis=replace(execution.analysis, methodology_version=methodology_version),
        )

    async def asyncTearDown(self) -> None:
        pool = self.store._require_pool()
        await pool.execute(
            "DELETE FROM analysis_snapshots WHERE analysis_id = ANY($1::text[])",
            self.analysis_ids,
        )
        await self.store.close()

    async def test_persists_and_reads_a_snapshot(self) -> None:
        await self.store.save(self.analysis_ids[0], self.execution)

        snapshot = await self.store.get(self.analysis_ids[0])

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.report["analysis"]["id"], self.analysis_ids[0])
        expected_heading = f"Анализ {chr(96)}{self.analysis_ids[0]}{chr(96)}"
        self.assertIn(expected_heading, snapshot.markdown)

    async def test_returns_latest_snapshot_per_methodology(self) -> None:
        old_id = self.analysis_ids[0]
        latest_id = f"integration-{uuid4().hex}"
        version_two_id = f"integration-{uuid4().hex}"
        self.analysis_ids.extend((latest_id, version_two_id))
        await self.store.save(
            old_id,
            self._execution("repo-42", self.timestamp - timedelta(days=1)),
        )
        await self.store.save(latest_id, self.execution)
        await self.store.save(
            version_two_id,
            self._execution(
                "repo-42",
                self.timestamp + timedelta(days=1),
                methodology_version="v2",
            ),
        )

        snapshots = await self.store.list_latest_for_repositories(("repo-42",))

        self.assertEqual(
            [
                (snapshot.analysis_id, snapshot.snapshot.report["analysis"]["methodologyVersion"])
                for snapshot in snapshots
            ],
            [(latest_id, "v1"), (version_two_id, "v2")],
        )
