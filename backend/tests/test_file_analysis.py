import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from backend.app.analyzers import code_health, documentation
from backend.app.contracts import AnalysisContext, DataStatus, RepositoryRef
from backend.app.integrations.git_repository import (
    MAX_FILE_BYTES,
    GitCloneError,
    GitTreeLimitError,
    LocalGitRepository,
    sourcecraft_clone_url,
)


def _tree_record(relative_path: str, size: int) -> bytes:
    return f"100644 blob {'a' * 40} {size}\t{relative_path}\0".encode()


class FileAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        now = datetime.now(UTC)
        self.context = AnalysisContext(RepositoryRef("repo", "team", "service"), "a" * 40, now, now, now)

    def test_clone_url_is_built_from_safe_slugs_only(self) -> None:
        self.assertEqual(
            sourcecraft_clone_url("team", "service"),
            "https://api.sourcecraft.tech/team/service.git",
        )
        with self.assertRaises(ValueError):
            sourcecraft_clone_url("team/other", "service")
        with self.assertRaises(ValueError):
            LocalGitRepository("https://attacker.example/team/service.git")
        with self.assertRaises(ValueError):
            LocalGitRepository("https://api.sourcecraft.tech/team/other/path.git")
        with self.assertRaises(ValueError):
            LocalGitRepository("https://api.sourcecraft.tech/team/service.git?token=unsafe")

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
            "http.https://api.sourcecraft.tech/.extraHeader",
        )
        self.assertEqual(run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("http.followRedirects=false", run.call_args.args[0])

    def test_redact_hides_clone_url_and_token_from_a_worker_error(self) -> None:
        url = sourcecraft_clone_url("team", "service")
        repository = LocalGitRepository(url, auth_token="secret-token")

        safe = repository.redact(f"git failed for {url}: secret-token")

        self.assertNotIn("secret-token", safe)
        self.assertNotIn("api.sourcecraft.tech/team/service.git", safe)
        self.assertEqual(safe, "git failed for <repo>: <token>")

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
        self.assertEqual(
            (
                facts["total_files"],
                facts["todo_count"],
                facts["fixme_count"],
                facts["skipped_large_files"],
            ),
            (2, 1, 1, 1),
        )

    def test_collect_ignores_javascript_regex_but_keeps_real_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "regex.js").write_text(
                "const re = /[//] TODO/; // FIXME: real comment\n"
                "const block = /[/*] FIXME/;\n"
                "function build() { return /[//] TODO/; }\n"
                "if (ok) /[//] TODO/.test(value);\n"
                "if ((ok && check())) {} /[//] TODO/.test(value);\n"
                "export default /[//] TODO/;\n"
                "class Derived extends /[//] TODO/.constructor {}\n"
                "const grouped = (left + right) / divisor; // TODO: grouped division\n"
                "const objectRatio = {value: 2} / divisor; // TODO: object division\n"
                "const propertyRatio = obj.if(value) / divisor; // TODO: property call\n"
                "const ratio = left / right; // TODO: real comment\n",
                encoding="utf-8",
            )
            repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
            repository.temp_dir = directory
            facts = code_health.collect(repository)

        self.assertEqual(facts["total_files"], 1)
        self.assertEqual(facts["todo_count"], 4)
        self.assertEqual(facts["fixme_count"], 1)
        self.assertEqual(facts["files_with_debt"], 1)

    @patch("backend.app.integrations.git_repository.subprocess.run")
    def test_clone_uses_blobless_tree_without_checkout(self, run) -> None:
        repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
        run.side_effect = (
            subprocess.CompletedProcess(["git"], 0),
            subprocess.CompletedProcess(["git"], 0, stdout=b"a" * 40 + b"\n"),
        )

        with tempfile.TemporaryDirectory() as directory, patch(
            "backend.app.integrations.git_repository.tempfile.mkdtemp",
            return_value=directory,
        ):
            repository.clone()

        clone_command = run.call_args_list[0].args[0]
        self.assertIn("--filter=blob:none", clone_command)
        self.assertIn("--no-checkout", clone_command)
        self.assertNotIn("checkout", clone_command)

    @patch("backend.app.integrations.git_repository.subprocess.run")
    def test_clone_fetches_a_requested_commit_without_checkout_or_all_blobs(self, run) -> None:
        repository = LocalGitRepository(
            sourcecraft_clone_url("team", "service"),
            ref="a" * 40,
        )
        run.side_effect = (
            subprocess.CompletedProcess(["git"], 0),
            subprocess.CompletedProcess(["git"], 0),
            subprocess.CompletedProcess(["git"], 0, stdout=b"a" * 40 + b"\n"),
        )

        with tempfile.TemporaryDirectory() as directory, patch(
            "backend.app.integrations.git_repository.tempfile.mkdtemp",
            return_value=directory,
        ):
            repository.clone()

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn("--filter=blob:none", commands[0])
        self.assertIn("--no-checkout", commands[0])
        self.assertIn("fetch", commands[1])
        self.assertIn("--filter=blob:none", commands[1])
        self.assertFalse(any("checkout" in command for command in commands))

    def test_blobless_reader_rejects_large_blob_before_git_can_fetch_it(self) -> None:
        repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
        repository.temp_dir = "/tmp/repo"
        repository._treeish = "a" * 40

        with patch.object(repository, "_run_git_bytes") as run_git:
            content = repository.read_file("large.py", expected_size=MAX_FILE_BYTES + 1)

        self.assertIsNone(content)
        run_git.assert_not_called()

    def test_blobless_tree_stops_at_entry_limit_without_consuming_the_whole_tree(self) -> None:
        repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
        repository.temp_dir = "/tmp/repo"
        repository._treeish = "a" * 40
        consumed: list[str] = []

        def chunks():
            for filename in ("first.py", "second.py", "third.py", "fourth.py"):
                consumed.append(filename)
                yield _tree_record(filename, 7)

        with (
            patch.object(repository, "_iter_git_tree_chunks", return_value=chunks()),
            self.assertRaises(GitTreeLimitError),
        ):
            list(
                repository.iter_file_entries(
                    excluded_directories=frozenset(),
                    max_entries=2,
                    max_tree_bytes=10_000,
                )
            )

        self.assertEqual(consumed, ["first.py", "second.py", "third.py"])

    def test_blobless_tree_stops_at_metadata_byte_limit(self) -> None:
        repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
        repository.temp_dir = "/tmp/repo"
        repository._treeish = "a" * 40
        record = _tree_record("app.py", 7)

        with (
            patch.object(repository, "_iter_git_tree_chunks", return_value=iter((record,))),
            self.assertRaises(GitTreeLimitError),
        ):
            list(
                repository.iter_file_entries(
                    excluded_directories=frozenset(),
                    max_entries=10,
                    max_tree_bytes=len(record) - 1,
                )
            )

    def test_blobless_tree_streams_entries_from_a_real_git_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("# TODO\n", encoding="utf-8")
            for arguments in (
                ["git", "init", "--quiet", directory],
                ["git", "-C", directory, "add", "app.py"],
            ):
                subprocess.run(arguments, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            treeish = subprocess.run(
                ["git", "-C", directory, "write-tree"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout.decode("ascii").strip()
            repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
            repository.temp_dir = directory
            repository._treeish = treeish

            entries = list(repository.iter_file_entries(excluded_directories=frozenset()))
            facts = code_health.collect(repository)

        self.assertEqual(entries[0].relative_path, "app.py")
        self.assertEqual(entries[0].size, 7)
        self.assertEqual((facts["total_files"], facts["todo_count"]), (1, 1))

    def test_blob_fetch_failure_returns_insufficient_sample_not_a_score(self) -> None:
        repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
        repository.temp_dir = "/tmp/repo"
        repository._treeish = "a" * 40

        with (
            patch.object(repository, "_iter_git_tree_chunks", return_value=iter((_tree_record("app.py", 7),))),
            patch.object(
                repository,
                "_run_git_bytes",
                side_effect=subprocess.TimeoutExpired(["git", "cat-file"], 1),
            ),
        ):
            facts = code_health.collect(repository)

        self.assertTrue(facts["truncated"])
        self.assertEqual(facts["total_files"], 0)
        result = code_health.evaluate(self.context, facts)
        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)

    def test_evaluate_is_reproducible(self) -> None:
        docs = documentation.evaluate(
            self.context,
            {
                "has_readme": True,
                "has_contributing": False,
                "has_codeowners": False,
                "has_license": False,
                "has_shortcuts": True,
            },
        )
        health = code_health.evaluate(
            self.context,
            {"total_files": 100, "todo_count": 1, "fixme_count": 1, "files_with_debt": 1},
        )
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

        self.assertTrue(facts["truncated"])
        self.assertEqual(facts["total_files"], 2)
        result = code_health.evaluate(self.context, facts)
        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        self.assertEqual(result.reason, "code_health_scan_limit_exceeded")

    def test_binary_source_files_consume_byte_budget_before_they_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first.py").write_bytes(b"\x00binary")
            (root / "second.js").write_bytes(b"\x00binary")
            repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
            repository.temp_dir = directory
            with patch.object(repository, "read_file", wraps=repository.read_file) as read_file:
                facts = code_health.collect(repository, max_total_bytes=7)

        self.assertTrue(facts["truncated"])
        self.assertEqual(facts["total_files"], 0)
        self.assertEqual(read_file.call_count, 1)

    def test_resource_limits_must_be_positive(self) -> None:
        repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
        with self.assertRaises(ValueError):
            code_health.collect(repository, max_files=0)
        with self.assertRaises(ValueError):
            code_health.collect(repository, max_total_bytes=0)

    def test_resource_limits_reject_non_integer_values_and_booleans(self) -> None:
        repository = LocalGitRepository(sourcecraft_clone_url("team", "service"))
        invalid_values = (True, 1.0, float("inf"), float("nan"), "100")
        for limit_name in ("max_files", "max_total_bytes"):
            for value in invalid_values:
                with (
                    self.subTest(limit_name=limit_name, value=value),
                    self.assertRaises(TypeError),
                ):
                    code_health.collect(repository, **{limit_name: value})

    def test_empty_source_tree_is_unavailable(self) -> None:
        result = code_health.evaluate(self.context, {"total_files": 0})
        self.assertEqual(result.status, DataStatus.NOT_APPLICABLE)


if __name__ == "__main__":
    unittest.main()
