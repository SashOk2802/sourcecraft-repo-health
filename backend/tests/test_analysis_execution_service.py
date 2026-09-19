from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.analysis import (
    AnalysisExecutionService,
    AnalyzerRegistration,
    InMemoryAnalysisJobStore,
    InMemoryAnalysisStore,
)
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef


class AnalysisExecutionServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_runs_analysis_saves_snapshot_and_marks_partial(self) -> None:
        timestamp = datetime(2026, 9, 19, 10, tzinfo=UTC)
        context = AnalysisContext(
            repository=RepositoryRef("repo-42", "team", "platform-api"),
            commit_sha="abc123",
            analyzed_at=timestamp,
            period_start=timestamp,
            period_end=timestamp,
        )
        clock_values = iter(
            (
                timestamp,
                timestamp + timedelta(seconds=1),
                timestamp + timedelta(seconds=2),
            )
        )
        snapshot_store = InMemoryAnalysisStore()
        job_store = InMemoryAnalysisJobStore()
        service = AnalysisExecutionService(
            job_store=job_store,
            snapshot_store=snapshot_store,
            clock=lambda: next(clock_values),
        )

        await service.create_job(context, "analysis-42")
        finished_job = await service.execute(
            analysis_id="analysis-42",
            context=context,
            analyzers=(activity_registration(),),
        )

        snapshot = await snapshot_store.get("analysis-42")
        self.assertEqual(finished_job.status.value, "partial")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.report["analysis"]["status"], "partial")
        self.assertEqual(snapshot.report["score"], 80)


def activity_registration() -> AnalyzerRegistration:
    def evaluate(_: AnalysisContext) -> CategoryResult:
        return CategoryResult(
            category="activity",
            status=DataStatus.MEASURED,
            score=80,
            summary="Активность измерена.",
        )

    return AnalyzerRegistration("activity", evaluate)
