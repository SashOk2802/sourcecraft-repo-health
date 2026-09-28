"""Проверяет живой прогон Documentation и Code health без сети."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
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

    def test_main_passes_the_api_token_to_git(self) -> None:
        """Git получает ту же строку, что и API-клиент, без accessor на клиенте."""

        script = self.script
        token = "pat-test-token"
        captured: dict[str, str] = {}
        resolve = script.SourceCraftClient.resolve_git_clone_url
        real_repository = script.LocalGitRepository

        class RecordingClient:
            resolve_git_clone_url = staticmethod(resolve)

            def __init__(self, received: str) -> None:
                captured["client_token"] = received

            def get_json(self, path: str) -> dict[str, object]:
                captured["path"] = path
                return {"id": "9", "web_url": "https://sourcecraft.dev/team/platform-api"}

            def close(self) -> None:
                captured["closed"] = "yes"

        class RecordingRepository(real_repository):
            def __init__(
                self,
                repo_url: str,
                ref: str | None = None,
                *,
                auth_token: str | None = None,
            ) -> None:
                super().__init__(repo_url, ref, auth_token=auth_token)
                captured["auth_token"] = auth_token or ""
                captured["ref"] = ref or ""
                captured["repo_url"] = repo_url

            def clone(self) -> str:
                self.temp_dir = captured["workspace"]
                return self.temp_dir

        def remote_head_sha(repo_url: str, received: str) -> str:
            captured["remote_token"] = received
            captured["remote_url"] = repo_url
            return "abc123"

        originals = (
            script.read_token,
            script.SourceCraftClient,
            script.LocalGitRepository,
            script.remote_head_sha,
        )
        workspace = tempfile.mkdtemp()
        try:
            root = Path(workspace)
            (root / "README.md").write_text("# Demo\n\n## Запуск\n", encoding="utf-8")
            (root / "service.py").write_text("def run() -> None:\n    return None\n", encoding="utf-8")
            captured["workspace"] = workspace
            script.read_token = lambda: token
            script.SourceCraftClient = RecordingClient
            script.LocalGitRepository = RecordingRepository
            script.remote_head_sha = remote_head_sha
            with redirect_stdout(StringIO()):
                code = script.main(["script", "team/platform-api"])
        finally:
            (
                script.read_token,
                script.SourceCraftClient,
                script.LocalGitRepository,
                script.remote_head_sha,
            ) = originals
            if os.path.isdir(workspace):
                shutil.rmtree(workspace)

        self.assertEqual(code, 0)
        self.assertEqual(captured["path"], "/repos/team/platform-api")
        self.assertEqual(captured["client_token"], token)
        self.assertEqual(captured["remote_token"], token)
        self.assertEqual(captured["auth_token"], token)
        self.assertEqual(captured["ref"], "abc123")
        self.assertEqual(captured["closed"], "yes")
        self.assertEqual(
            captured["repo_url"],
            "https://sourcecraft.dev/team/platform-api.git",
        )
        self.assertEqual(captured["remote_url"], captured["repo_url"])

    def test_remote_head_failure_does_not_keep_the_token_in_the_exception_chain(self) -> None:
        script = self.script
        command = ["git", "-c", "http.extraheader=AUTHORIZATION: Bearer SECRET", "ls-remote"]

        def fail(*args: object, **kwargs: object) -> None:
            raise subprocess.CalledProcessError(128, command)

        original = script.subprocess.run
        script.subprocess.run = fail
        try:
            with self.assertRaises(SystemExit) as caught:
                script.remote_head_sha("https://sourcecraft.dev/team/platform-api.git", "SECRET")
        finally:
            script.subprocess.run = original

        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn("SECRET", str(caught.exception))


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
