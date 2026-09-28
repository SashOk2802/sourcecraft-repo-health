"""Проверяет живой прогон Documentation и Code health без сети."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from backend.app.contracts import AnalysisContext, DataStatus, RepositoryRef
from backend.app.integrations.git_repository import LocalGitRepository

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_file_analyzers_live.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_file_analyzers_live", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"не удалось загрузить {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SplitRepositorySlugTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = load_script()

    def test_accepts_org_and_repo(self) -> None:
        self.assertEqual(self.script.split_repository_slug("team/platform-api"), ("team", "platform-api"))

    def test_rejects_a_value_without_one_slash(self) -> None:
        self.assertIsNone(self.script.split_repository_slug("platform-api"))
        self.assertIsNone(self.script.split_repository_slug("team/"))
        self.assertIsNone(self.script.split_repository_slug("/platform-api"))
        self.assertIsNone(self.script.split_repository_slug("org/team/platform-api"))


class AnalyzeWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = load_script()

    def test_measures_both_categories_from_a_prepared_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text("# Demo\n\n## Запуск\n", encoding="utf-8")
            (root / "service.py").write_text("def run() -> None:\n    return None\n", encoding="utf-8")
            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory
            documentation_result, code_health_result = self.script.analyze_workspace(
                repository,
                analysis_context(),
            )

        self.assertIs(documentation_result.status, DataStatus.MEASURED)
        self.assertEqual(documentation_result.score, 50)
        self.assertIs(code_health_result.status, DataStatus.MEASURED)
        self.assertEqual(code_health_result.score, 100)


class LiveScriptMainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = load_script()

    def test_main_rejects_a_repository_argument_that_is_not_org_repo(self) -> None:
        self.assertEqual(self.script.main(["script"]), 2)
        self.assertEqual(self.script.main(["script", "platform-api"]), 2)
        self.assertEqual(self.script.main(["script", "org/team/platform-api"]), 2)


def analysis_context() -> AnalysisContext:
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    return AnalysisContext(
        repository=RepositoryRef(
            id="repo-42",
            organization_slug="team",
            repository_slug="platform-api",
        ),
        commit_sha="abc123",
        analyzed_at=now,
        period_start=now,
        period_end=now,
    )
