from __future__ import annotations

import unittest
from datetime import UTC, datetime

from backend.app.analysis import InMemoryAnalysisStore, run_analysis
from backend.app.contracts import AnalysisContext, RepositoryRef


class InMemoryAnalysisStoreTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_saved_snapshot_can_be_retrieved_by_identifier(self) -> None:
        store = InMemoryAnalysisStore()

        await store.save("analysis-42", self.execution, owner_subject="user-42")
        snapshot = await store.get("analysis-42")

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.report["analysis"]["id"], "analysis-42")
        self.assertIn("Анализ " + chr(96) + "analysis-42" + chr(96), snapshot.markdown)

    async def test_rejects_identifier_with_a_slash(self) -> None:
        store = InMemoryAnalysisStore()

        with self.assertRaisesRegex(ValueError, "URL-safe characters"):
            await store.save("analysis/part", self.execution, owner_subject="user-42")

    async def test_snapshot_identifier_cannot_be_reused(self) -> None:
        store = InMemoryAnalysisStore()
        await store.save("analysis-42", self.execution, owner_subject="user-42")

        with self.assertRaisesRegex(ValueError, "analysis_id already exists"):
            await store.save(
                "analysis-42",
                self.execution,
                owner_subject="user-42",
            )
