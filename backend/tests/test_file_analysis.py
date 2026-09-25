import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from backend.app.analyzers import code_health, documentation
from backend.app.contracts import AnalysisContext, DataStatus, RepositoryRef
from backend.app.integrations.git_repository import (
    GitCloneError,
    LocalGitRepository,
    sourcecraft_clone_url,
)


class FileAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        now = datetime.now(UTC)
        self.context = AnalysisContext(RepositoryRef("repo", "team", "service"), "a" * 40, now, now, now)

    def test_clone_url_is_built_from_safe_slugs_only(self) -> None:
        self.assertEqual(
            sourcecraft_clone_url("team", "service"),
            "https://git@git.sourcecraft.dev/team/service.git",
        )
        with self.assertRaises(ValueError):
            sourcecraft_clone_url("team/other", "service")
        with self.assertRaises(ValueError):
            LocalGitRepository("https://attacker.example/team/service.git")
        with self.assertRaises(ValueError):
            LocalGitRepository("https://git.sourcecraft.dev/team/other/path.git")
        with self.assertRaises(ValueError):
            LocalGitRepository("https://git.sourcecraft.dev/team/service.git")

    @patch("backend.app.integrations.git_repository.subprocess.run")
    def test_clone_keeps_token_out_of_command_and_error(self, run) -> None:
        run.side_effect = subprocess.CalledProcessError(1, ["git"], stderr="secret-token")
        repository = LocalGitRepository(sourcecraft_clone_url("team", "service"), auth_token="secret-token")
        with self.assertRaises(GitCloneError) as error:
            repository.clone()
        self.assertNotIn("secret-token", str(error.exception))
        self.assertNotIn("secret-token", run.call_args.args[0])
        self.assertEqual(
            run.call_args.kwargs["env"]["GIT_CONFIG_VALUE_0"],
            "Authorization: Basic Z2l0OnNlY3JldC10b2tlbg==",
        )
        self.assertEqual(
            run.call_args.kwargs["env"]["GIT_CONFIG_KEY_0"],
            "http.https://git.sourcecraft.dev/.extraHeader",
        )
        self.assertEqual(run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("http.followRedirects=false", run.call_args.args[0])

    def test_collect_ignores_strings_vendor_and_large_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text('label = "TODO FIXME"\n# TODO: write tests\n', encoding="utf-8")
            (root / "web.js").write_text('const label = "TODO"; // FIXME: replace\n', encoding="utf-8")
            (root / "vendor").mkdir()
            (root / "vendor" / "ignored.py").write_text("# FIXME\n", encoding="utf-8")
            (root / "large.py").write_bytes(b"# TODO\n" + b"x" * (512 * 1024))
            repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
            repository.temp_dir = directory
            facts = code_health.collect(repository)
        self.assertEqual((facts.total_files, facts.todo_count, facts.fixme_count, facts.skipped_large_files), (2, 1, 1, 1))

    def test_collect_ignores_javascript_regex_but_keeps_real_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "regex.js").write_text(
                "const re = /[//] TODO/; // FIXME: real comment\n"
                "const block = /[/*] FIXME/;\n"
                "function build() { return /[//] TODO/; }\n"
                "const ratio = left / right; // TODO: real comment\n",
                encoding="utf-8",
            )
            repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
            repository.temp_dir = directory
            facts = code_health.collect(repository)

        self.assertEqual(facts.total_files, 1)
        self.assertEqual(facts.todo_count, 1)
        self.assertEqual(facts.fixme_count, 1)
        self.assertEqual(facts.files_with_debt, 1)

    def test_evaluate_is_reproducible(self) -> None:
        docs = documentation.evaluate(self.context, documentation.DocumentationFacts(True, False, False, False, True))
        health = code_health.evaluate(self.context, code_health.CodeHealthFacts(2, 1, 1, 1, 0))
        self.assertEqual((docs.status, docs.score), (DataStatus.MEASURED, 50.0))
        self.assertEqual((health.status, health.score), (DataStatus.MEASURED, 94.0))

    def test_resource_limit_returns_insufficient_sample_instead_of_partial_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(3):
                (root / f"file_{index}.py").write_text("# TODO\n", encoding="utf-8")
            repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
            repository.temp_dir = directory
            facts = code_health.collect(repository, max_files=2)

        self.assertTrue(facts.truncated)
        self.assertEqual(facts.total_files, 2)
        result = code_health.evaluate(self.context, facts)
        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        self.assertEqual(result.reason, "code_health_scan_limit_exceeded")

    def test_resource_limits_must_be_positive(self) -> None:
        repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
        with self.assertRaises(ValueError):
            code_health.collect(repository, max_files=0)
        with self.assertRaises(ValueError):
            code_health.collect(repository, max_total_bytes=0)

    def test_empty_source_tree_is_unavailable(self) -> None:
        result = code_health.evaluate(self.context, code_health.CodeHealthFacts(0, 0, 0, 0, 0))
        self.assertEqual(result.status, DataStatus.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
