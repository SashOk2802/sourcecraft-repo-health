from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from backend.app.analyzers import code_health, documentation
from backend.app.contracts import AnalysisContext, DataStatus, RepositoryRef
from backend.app.integrations.git_repository import LocalGitRepository


def analysis_context() -> AnalysisContext:
    now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    return AnalysisContext(
        repository=RepositoryRef(
            id="repo-42",
            organization_slug="team",
            repository_slug="platform-api",
        ),
        commit_sha="a" * 40,
        analyzed_at=now,
        period_start=now,
        period_end=now,
    )


class CodeHealthCollectionTest(unittest.TestCase):
    def test_counts_markers_only_in_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text(
                'message = "TODO: not a comment"\n# TODO: add validation\n# FIXME: retry\n',
                encoding="utf-8",
            )
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = directory
            result = code_health.evaluate(analysis_context(), code_health.collect(repository))

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 0.0)
        metrics = {metric.code: metric.value for metric in result.metrics}
        self.assertEqual(metrics["todo_count"], 1)
        self.assertEqual(metrics["fixme_count"], 1)

    def test_malformed_python_does_not_receive_a_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.py").write_text('""" TODO: unterminated', encoding="utf-8")
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = directory
            result = code_health.evaluate(analysis_context(), code_health.collect(repository))

        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)

    def test_exceeding_scan_budget_does_not_create_partial_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("# TODO\n", encoding="utf-8")
            (root / "b.py").write_text("# TODO\n", encoding="utf-8")
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = directory
            facts = code_health.collect(repository, max_files=1)
            result = code_health.evaluate(analysis_context(), facts)

        self.assertTrue(facts["truncated"])
        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)

    def test_dense_comments_keep_evidence_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker_count = 10_000
            (root / "dense.py").write_text(
                "# TODO FIXME\n" * marker_count,
                encoding="utf-8",
            )
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = directory
            facts = code_health.collect(repository)

        self.assertEqual(facts["todo_count"], marker_count)
        self.assertEqual(facts["fixme_count"], marker_count)
        self.assertLessEqual(len(facts["occurrences"]), code_health.MAX_EVIDENCE_ENTRIES)

    def test_unreadable_large_source_does_not_count_as_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.py").write_bytes(b"# TODO\n" * (code_health.MAX_FILE_READ_BYTES // 7 + 1))
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = directory
            result = code_health.evaluate(analysis_context(), code_health.collect(repository))

        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)


class DocumentationFileDetectionTest(unittest.TestCase):
    def test_directory_is_not_documentation_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").mkdir()
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = directory
            facts = documentation.collect(repository)

        self.assertFalse(facts["has_readme"])


if __name__ == "__main__":
    unittest.main()
