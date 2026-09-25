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
        context = _context("repo-42", timestamp)
        service, job_store, snapshot_store = _service(timestamp)

        await service.create_job(context, "analysis-42", owner_subject="user-42")
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
        self.assertEqual(
            (await job_store.get("analysis-42")).repository_id,
            context.repository.id,
        )

    async def test_rejects_context_for_another_repository_before_running_analyzers(
        self,
    ) -> None:
        timestamp = datetime(2026, 9, 19, 10, tzinfo=UTC)
        expected_context = _context("repo-42", timestamp)
        foreign_context = _context("repo-43", timestamp)
        service, job_store, snapshot_store = _service(timestamp)
        evaluation_calls: list[str] = []

        def must_not_run(_: AnalysisContext) -> CategoryResult:
            evaluation_calls.append("called")
            raise AssertionError("Analyzer must not run for another repository.")

        await service.create_job(expected_context, "analysis-42", owner_subject="user-42")
        rejected_job = await service.execute(
            analysis_id="analysis-42",
            context=foreign_context,
            analyzers=(AnalyzerRegistration("activity", must_not_run),),
        )

        self.assertEqual(rejected_job.status.value, "failed")
        self.assertEqual(rejected_job.error_code, "repository_mismatch")
        self.assertEqual(evaluation_calls, [])
        self.assertIsNone(await snapshot_store.get("analysis-42"))
        self.assertEqual((await job_store.get("analysis-42")).status.value, "failed")


def _service(
    timestamp: datetime,
) -> tuple[AnalysisExecutionService, InMemoryAnalysisJobStore, InMemoryAnalysisStore]:
    clock_values = iter(
        (
            timestamp,
            timestamp + timedelta(seconds=1),
            timestamp + timedelta(seconds=2),
        )
    )
    job_store = InMemoryAnalysisJobStore()
    snapshot_store = InMemoryAnalysisStore()
    return (
        AnalysisExecutionService(
            job_store=job_store,
            snapshot_store=snapshot_store,
            clock=lambda: next(clock_values),
        ),
        job_store,
        snapshot_store,
    )


def _context(repository_id: str, timestamp: datetime) -> AnalysisContext:
    return AnalysisContext(
        repository=RepositoryRef(repository_id, "team", "platform-api"),
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
