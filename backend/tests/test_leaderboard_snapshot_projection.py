from __future__ import annotations

import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from backend.app.analysis import AnalysisSnapshot, StoredAnalysisSnapshot, run_analysis
from backend.app.analysis.runner import AnalyzerRegistration
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef
from backend.app.leaderboard import PublicRepositoryMetadata, project_public_snapshot
from backend.app.reporting import build_report_payload
from backend.app.scoring.methodology import CATEGORY_WEIGHTS


class LeaderboardSnapshotProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.timestamp = datetime(2026, 9, 25, 12, tzinfo=UTC)
        self.metadata = PublicRepositoryMetadata(
            repository_id="repo-42",
            organization_slug="team",
            repository_slug="platform-api",
            url="https://sourcecraft.dev/team/platform-api",
            description="Публичный API платформы",
            language="Python",
            likes=None,
            last_activity_at=self.timestamp - timedelta(days=1),
        )
        self.stored_snapshot = self._stored_snapshot()

    def _stored_snapshot(
        self,
        *,
        analyzers: tuple[AnalyzerRegistration, ...] | None = None,
        analysis_id: str = "analysis-42",
    ) -> StoredAnalysisSnapshot:
        context = AnalysisContext(
            repository=RepositoryRef(
                "repo-42",
                "team",
                "platform-api",
                "https://sourcecraft.dev/team/platform-api",
            ),
            commit_sha="a" * 40,
            analyzed_at=self.timestamp,
            period_start=self.timestamp - timedelta(days=365),
            period_end=self.timestamp,
        )
        execution = run_analysis(
            context,
            analyzers
            if analyzers is not None
            else (
                AnalyzerRegistration(
                    "activity",
                    lambda _: CategoryResult(
                        category="activity",
                        status=DataStatus.MEASURED,
                        score=91,
                        summary="Репозиторий активно развивается.",
                    ),
                ),
            ),
        )
        report = build_report_payload(execution, analysis_id=analysis_id)
        return StoredAnalysisSnapshot(
            analysis_id=analysis_id,
            snapshot=AnalysisSnapshot(report=report, markdown="# Отчёт\n"),
        )

    def test_projects_verified_public_snapshot_with_category_briefs(self) -> None:
        entry = project_public_snapshot(self.metadata, self.stored_snapshot)

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.analysis_id, "analysis-42")
        self.assertEqual(entry.repository.name, "team/platform-api")
        self.assertEqual(entry.methodology_version, "v1")
        self.assertEqual(entry.score, 91)
        self.assertTrue(entry.is_preliminary)
        self.assertIsNotNone(entry.candidate)
        assert entry.candidate is not None
        self.assertIsNone(entry.candidate.likes)
        self.assertEqual(entry.coverage, 0.15)
        self.assertFalse(entry.score_limited)
        self.assertEqual(
            [(category.code, category.status, category.score) for category in entry.categories],
            [
                ("security", "unavailable", None),
                ("cicd", "unavailable", None),
                ("documentation", "unavailable", None),
                ("activity", "measured", 91.0),
                ("issues", "unavailable", None),
                ("code_health", "unavailable", None),
            ],
        )

    def test_excludes_snapshot_when_public_catalog_identity_does_not_match(self) -> None:
        report = deepcopy(self.stored_snapshot.snapshot.report)
        report["repository"]["organizationSlug"] = "another-team"
        mismatched = replace(
            self.stored_snapshot,
            snapshot=AnalysisSnapshot(report=report, markdown="# Отчёт\n"),
        )

        entry = project_public_snapshot(self.metadata, mismatched)

        self.assertIsNone(entry)

    def test_keeps_snapshot_without_calculated_score_in_preliminary_block(self) -> None:
        empty_snapshot = self._stored_snapshot(analyzers=(), analysis_id="analysis-empty")

        entry = project_public_snapshot(self.metadata, empty_snapshot)

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertIsNone(entry.score)
        self.assertTrue(entry.is_preliminary)
        self.assertIsNone(entry.candidate)

    def test_keeps_not_applicable_report_without_coverage_in_preliminary_block(self) -> None:
        analyzers = tuple(
            AnalyzerRegistration(
                category,
                lambda _, category=category: CategoryResult(
                    category=category,
                    status=DataStatus.NOT_APPLICABLE,
                    score=None,
                    summary="Категория неприменима.",
                ),
            )
            for category in CATEGORY_WEIGHTS
        )
        snapshot = self._stored_snapshot(analyzers=analyzers, analysis_id="analysis-na")

        entry = project_public_snapshot(self.metadata, snapshot)

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertIsNone(entry.score)
        self.assertIsNone(entry.coverage)
        self.assertFalse(entry.is_preliminary)
        self.assertIsNone(entry.candidate)

    def test_marks_score_limit_from_stored_report(self) -> None:
        report = deepcopy(self.stored_snapshot.snapshot.report)
        report["analysis"]["scoreLimit"] = {
            "value": 60,
            "uncappedScore": 91,
            "code": "critical_security_vulnerability",
            "summary": "Score ограничен.",
        }
        limited = replace(
            self.stored_snapshot,
            snapshot=AnalysisSnapshot(report=report, markdown="# Отчёт\n"),
        )

        entry = project_public_snapshot(self.metadata, limited)

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertTrue(entry.score_limited)

    def test_rejects_status_that_disagrees_with_preliminary_flag(self) -> None:
        report = deepcopy(self.stored_snapshot.snapshot.report)
        report["analysis"]["status"] = "completed"
        inconsistent = replace(
            self.stored_snapshot,
            snapshot=AnalysisSnapshot(report=report, markdown="# Отчёт\n"),
        )

        with self.assertRaisesRegex(ValueError, "status"):
            project_public_snapshot(self.metadata, inconsistent)

    def test_rejects_non_https_public_repository_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            PublicRepositoryMetadata(
                repository_id="repo-42",
                organization_slug="team",
                repository_slug="platform-api",
                url="http://sourcecraft.dev/team/platform-api",
            )
