from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest import mock

from backend.app.analysis import InMemoryAnalysisStore, run_analysis
from backend.app.contracts import AnalysisContext, RepositoryRef


class InMemoryAnalysisStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.timestamp = datetime(2026, 9, 18, tzinfo=UTC)
        self.execution = self._execution("repo-42", self.timestamp)

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

    async def test_saved_snapshot_can_be_retrieved_by_identifier(self) -> None:
        store = InMemoryAnalysisStore()

        await store.save("analysis-42", self.execution)
        snapshot = await store.get("analysis-42")

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.report["analysis"]["id"], "analysis-42")
        self.assertIn("Анализ " + chr(96) + "analysis-42" + chr(96), snapshot.markdown)

    async def test_reads_snapshots_by_a_bounded_set_of_identifiers(self) -> None:
        store = InMemoryAnalysisStore()
        await store.save("analysis-first", self.execution)
        await store.save("analysis-second", self._execution("repo-other", self.timestamp))

        snapshots = await store.list_for_analysis_ids((" analysis-first ", "missing"))

        self.assertEqual([snapshot.analysis_id for snapshot in snapshots], ["analysis-first"])

    async def test_returns_latest_snapshot_for_every_repository_and_methodology(self) -> None:
        store = InMemoryAnalysisStore()
        await store.save(
            "analysis-v1-old",
            self._execution("repo-42", self.timestamp - timedelta(days=2)),
        )
        await store.save(
            "analysis-v1-new",
            self._execution("repo-42", self.timestamp),
        )
        await store.save(
            "analysis-v2",
            self._execution(
                "repo-42",
                self.timestamp + timedelta(days=1),
                methodology_version="v2",
            ),
        )
        await store.save(
            "analysis-other",
            self._execution("repo-other", self.timestamp + timedelta(days=2)),
        )

        snapshots = await store.list_latest_for_repositories((" repo-42 ", "repo-42"))

        self.assertEqual(
            [
                (snapshot.analysis_id, snapshot.snapshot.report["analysis"]["methodologyVersion"])
                for snapshot in snapshots
            ],
            [("analysis-v1-new", "v1"), ("analysis-v2", "v2")],
        )

    async def test_returns_empty_result_for_no_repository_identifiers(self) -> None:
        store = InMemoryAnalysisStore()

        snapshots = await store.list_latest_for_repositories(())

        self.assertEqual(snapshots, ())

    async def test_rejects_blank_repository_identifier(self) -> None:
        store = InMemoryAnalysisStore()

        with self.assertRaisesRegex(ValueError, "nonblank strings"):
            await store.list_latest_for_repositories(("",))

    async def test_rejects_identifier_with_a_slash(self) -> None:
        store = InMemoryAnalysisStore()

        with self.assertRaisesRegex(ValueError, "URL-safe characters"):
            await store.save("analysis/part", self.execution)

    async def test_snapshot_identifier_cannot_be_reused(self) -> None:
        store = InMemoryAnalysisStore()
        await store.save("analysis-42", self.execution)

        with self.assertRaisesRegex(ValueError, "analysis_id already exists"):
            await store.save("analysis-42", self.execution)

    async def test_get_latest_for_repository_slug(self) -> None:
        store = InMemoryAnalysisStore()
        await store.save(
            "analysis-old",
            self._execution("repo-42", self.timestamp - timedelta(days=1)),
        )
        await store.save(
            "analysis-new",
            self._execution("repo-42", self.timestamp),
        )

        result = await store.get_latest_for_repository_slug("team", "repository-repo-42")
        self.assertIsNotNone(result)
        self.assertEqual(result.analysis_id, "analysis-new")

        missing = await store.get_latest_for_repository_slug("unknown-org", "unknown-repo")
        self.assertIsNone(missing)

        with mock.patch("backend.app.analysis.store._latest_snapshots", return_value=()):
            empty_result = await store.get_latest_for_repository_slug("team", "repository-repo-42")
            self.assertIsNone(empty_result)
