"""Контракт git-истории для будущего подключения Activity."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from backend.app.analyzers import activity
from backend.app.integrations.git_repository import (
    DEFAULT_MAX_HISTORY_COMMITS,
    GitCloneError,
    LocalGitRepository,
)


class CommitHistoryTest(unittest.TestCase):
    def test_real_repository_returns_committer_date_inside_the_window(self) -> None:
        since = datetime(2026, 9, 1, tzinfo=UTC)
        until = datetime(2026, 9, 30, tzinfo=UTC)
        author_outside = datetime(2026, 8, 1, tzinfo=UTC)
        committer_inside = datetime(2026, 9, 15, 9, 30, tzinfo=UTC)

        with tempfile.TemporaryDirectory() as temporary_directory:
            self._commit(temporary_directory, "first", committer_inside, author_outside)
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = temporary_directory
            result = repository.commit_history(since=since, until=until)

        self.assertFalse(result.truncated)
        self.assertEqual(len(result.commits), 1)
        self.assertEqual(result.commits[0].committed_at, committer_inside)
        self.assertNotEqual(result.commits[0].committed_at, author_outside)

    def test_real_repository_flags_truncation_at_the_limit(self) -> None:
        first = datetime(2026, 9, 10, tzinfo=UTC)
        second = datetime(2026, 9, 20, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary_directory:
            self._commit(temporary_directory, "first", first, first)
            self._commit(temporary_directory, "second", second, second)
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = temporary_directory
            limited = repository.commit_history(max_commits=1)
            complete = repository.commit_history(max_commits=DEFAULT_MAX_HISTORY_COMMITS)

        self.assertTrue(limited.truncated)
        self.assertEqual(len(limited.commits), 1)
        self.assertFalse(complete.truncated)
        self.assertEqual(len(complete.commits), 2)

    def test_missing_workspace_is_a_clone_error(self) -> None:
        repository = LocalGitRepository("https://example.invalid/repo.git")
        with self.assertRaises(GitCloneError):
            repository.commit_history()

    def test_shallow_clone_without_since_requests_full_unshallow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            git_dir = Path(temporary_directory) / ".git"
            git_dir.mkdir()
            (git_dir / "shallow").write_text("abc\n", encoding="utf-8")
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = temporary_directory
            calls: list[list[str]] = []

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch(
                "backend.app.integrations.git_repository.subprocess.run",
                side_effect=fake_run,
            ):
                repository.commit_history()

        fetch = next(command for command in calls if "fetch" in command)
        self.assertIn("--unshallow", fetch)
        self.assertNotIn("--shallow-since", fetch)

    def test_shallow_clone_with_since_fetches_only_that_window(self) -> None:
        since = datetime(2026, 3, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary_directory:
            git_dir = Path(temporary_directory) / ".git"
            git_dir.mkdir()
            (git_dir / "shallow").write_text("abc\n", encoding="utf-8")
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = temporary_directory
            calls: list[list[str]] = []

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch(
                "backend.app.integrations.git_repository.subprocess.run",
                side_effect=fake_run,
            ):
                repository.commit_history(since=since)

        fetch = next(command for command in calls if "fetch" in command)
        self.assertIn("--shallow-since", fetch)
        self.assertEqual(fetch[fetch.index("--shallow-since") + 1], since.isoformat())
        self.assertNotIn("--unshallow", fetch)

    def test_activity_does_not_call_commit_history(self) -> None:
        source = Path(activity.__file__).read_text(encoding="utf-8")
        self.assertNotIn("commit_history(", source)

    def _commit(
        self,
        repository: str,
        message: str,
        committer_date: datetime,
        author_date: datetime,
    ) -> None:
        git_dir = Path(repository) / ".git"
        if not git_dir.exists():
            self._run(repository, "init")
        marker = Path(repository) / "README.md"
        marker.write_text(message + "\n", encoding="utf-8")
        self._run(repository, "add", "README.md")
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_AUTHOR_NAME": "Repo Health Test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Repo Health Test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
                "GIT_AUTHOR_DATE": author_date.isoformat(),
                "GIT_COMMITTER_DATE": committer_date.isoformat(),
            }
        )
        self._run(repository, "commit", "-m", message, env=environment)

    def _run(self, repository: str, *arguments: str, env: dict[str, str] | None = None) -> None:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-C", repository, *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
