import unittest
from datetime import UTC, datetime

from backend.app.analysis import InMemoryAnalysisStore, run_analysis
from backend.app.contracts import AnalysisContext, RepositoryRef


class InMemoryAnalysisStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        timestamp = datetime(2026, 9, 18, tzinfo=UTC)
        context = AnalysisContext(
            repository=RepositoryRef("repo-42", "team", "platform-api"),
            commit_sha="abc123",
            analyzed_at=timestamp,
            period_start=timestamp,
            period_end=timestamp,
        )
        self.execution = run_analysis(context, ())

    def test_saved_snapshot_can_be_retrieved_by_identifier(self) -> None:
        store = InMemoryAnalysisStore()

        store.save("analysis-42", self.execution)

        self.assertIs(store.get("analysis-42"), self.execution)

    def test_snapshot_identifier_cannot_be_reused(self) -> None:
        store = InMemoryAnalysisStore()
        store.save("analysis-42", self.execution)

        with self.assertRaisesRegex(ValueError, "analysis_id already exists"):
            store.save("analysis-42", self.execution)
