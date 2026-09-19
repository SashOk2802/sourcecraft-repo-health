from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.analysis import (
    AnalysisJob,
    AnalysisJobStatus,
    AnalysisJobTransitionError,
    InMemoryAnalysisJobStore,
)


class AnalysisJobTest(unittest.TestCase):
    def setUp(self) -> None:
        self.created_at = datetime(2026, 9, 19, 10, tzinfo=UTC)

    def test_allows_queued_running_partial_lifecycle(self) -> None:
        queued = AnalysisJob.queued(
            analysis_id="analysis-42",
            repository_id="repo-42",
            created_at=self.created_at,
        )

        running = queued.started(self.created_at + timedelta(seconds=1))
        partial = running.finished(
            status=AnalysisJobStatus.PARTIAL,
            finished_at=self.created_at + timedelta(seconds=2),
        )

        self.assertEqual(partial.status, AnalysisJobStatus.PARTIAL)
        self.assertEqual(partial.started_at, self.created_at + timedelta(seconds=1))
        self.assertEqual(partial.finished_at, self.created_at + timedelta(seconds=2))

    def test_rejects_completion_before_job_starts(self) -> None:
        queued = AnalysisJob.queued(
            analysis_id="analysis-42",
            repository_id="repo-42",
            created_at=self.created_at,
        )

        with self.assertRaises(AnalysisJobTransitionError):
            queued.finished(
                status=AnalysisJobStatus.COMPLETED,
                finished_at=self.created_at + timedelta(seconds=1),
            )

    def test_failed_job_requires_safe_error_details(self) -> None:
        running = AnalysisJob.queued(
            analysis_id="analysis-42",
            repository_id="repo-42",
            created_at=self.created_at,
        ).started(self.created_at + timedelta(seconds=1))

        with self.assertRaisesRegex(ValueError, "failed jobs require"):
            running.finished(
                status=AnalysisJobStatus.FAILED,
                finished_at=self.created_at + timedelta(seconds=2),
            )


class InMemoryAnalysisJobStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_persists_state_transitions(self) -> None:
        created_at = datetime(2026, 9, 19, 10, tzinfo=UTC)
        store = InMemoryAnalysisJobStore()
        job = AnalysisJob.queued(
            analysis_id="analysis-42",
            repository_id="repo-42",
            created_at=created_at,
        )

        await store.create(job)
        await store.mark_running("analysis-42", created_at + timedelta(seconds=1))
        completed = await store.finish(
            "analysis-42",
            status=AnalysisJobStatus.COMPLETED,
            finished_at=created_at + timedelta(seconds=2),
        )

        self.assertEqual(completed.status, AnalysisJobStatus.COMPLETED)
        self.assertEqual((await store.get("analysis-42")).finished_at, completed.finished_at)
