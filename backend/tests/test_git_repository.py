"""Тесты чтения истории коммитов из LocalGitRepository.

Задача 5: git-модуль получает метод commit_history — авторские даты коммитов
для метрик частоты коммитов и активных недель категории Activity. Клон по
умолчанию shallow, поэтому проверяются и команды догрузки истории.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from backend.app.integrations.git_repository import (
    GitCloneError,
    GitOperationError,
    LocalGitRepository,
)

_REPO_URL = "https://sourcecraft.dev/team/platform-api.git"


class CommitHistoryTest(unittest.TestCase):
    """commit_history: парсинг дат, окна, shallow-догрузка и ошибки."""

    def test_commit_history_parses_author_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalGitRepository(_REPO_URL)
            repository.temp_dir = directory
            completed = subprocess.CompletedProcess(
                args=["git", "log"],
                returncode=0,
                stdout="1699999999\n1700000001\n",
            )
            with patch(
                "backend.app.integrations.git_repository.subprocess.run",
                return_value=completed,
            ) as mock_run:
                facts = repository.commit_history()

        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0].authored_at, datetime.fromtimestamp(1699999999, tz=UTC))
        self.assertEqual(facts[1].authored_at, datetime.fromtimestamp(1700000001, tz=UTC))

        command = mock_run.call_args.args[0]
        self.assertEqual(command[0], "git")
        self.assertIn("log", command)
        self.assertIn("--first-parent", command)
        self.assertIn("--format=%at", command)

    def test_commit_history_passes_since_until_and_limit(self) -> None:
        since = datetime(2026, 8, 1, tzinfo=UTC)
        until = datetime(2026, 9, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalGitRepository(_REPO_URL)
            repository.temp_dir = directory
            completed = subprocess.CompletedProcess(
                args=["git", "log"],
                returncode=0,
                stdout="1700000001\n",
            )
            with patch(
                "backend.app.integrations.git_repository.subprocess.run",
                return_value=completed,
            ) as mock_run:
                repository.commit_history(since=since, until=until, max_commits=5)

        command = mock_run.call_args.args[0]
        self.assertIn("--since", command)
        self.assertIn(since.isoformat(), command)
        self.assertIn("--until", command)
        self.assertIn(until.isoformat(), command)
        self.assertEqual(command[command.index("-n") + 1], "5")

    def test_commit_history_deepens_shallow_clone_with_date_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            git_dir = Path(directory) / ".git"
            git_dir.mkdir()
            (git_dir / "shallow").write_text("", encoding="utf-8")

            repository = LocalGitRepository(_REPO_URL)
            repository.temp_dir = directory
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="1700000001\n")
            calls: list[list[str]] = []

            def fake_run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess:
                calls.append(arguments)
                return completed

            with patch(
                "backend.app.integrations.git_repository.subprocess.run",
                side_effect=fake_run,
            ):
                repository.commit_history(since=datetime(2026, 8, 1, tzinfo=UTC))

        self.assertEqual(len(calls), 2)
        fetch = calls[0]
        self.assertEqual(fetch[0], "git")
        self.assertEqual(fetch[1], "-C")
        self.assertIn("fetch", fetch)
        self.assertIn("--shallow-since", fetch)
        self.assertIn("log", calls[1])

    def test_commit_history_unshallows_when_window_is_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            git_dir = Path(directory) / ".git"
            git_dir.mkdir()
            (git_dir / "shallow").write_text("", encoding="utf-8")

            repository = LocalGitRepository(_REPO_URL)
            repository.temp_dir = directory
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="1700000001\n")
            calls: list[list[str]] = []

            def fake_run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess:
                calls.append(arguments)
                return completed

            with patch(
                "backend.app.integrations.git_repository.subprocess.run",
                side_effect=fake_run,
            ):
                repository.commit_history()

        fetch = calls[0]
        self.assertIn("--unshallow", fetch)

    def test_commit_history_does_not_fetch_when_not_shallow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalGitRepository(_REPO_URL)
            repository.temp_dir = directory
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
            with patch(
                "backend.app.integrations.git_repository.subprocess.run",
                return_value=completed,
            ) as mock_run:
                repository.commit_history()

        self.assertEqual(len(mock_run.call_args_list), 1)
        command = mock_run.call_args.args[0]
        self.assertNotIn("fetch", command)
        self.assertIn("log", command)

    def test_commit_history_without_workspace_raises_clone_error(self) -> None:
        repository = LocalGitRepository(_REPO_URL)
        with self.assertRaises(GitCloneError):
            repository.commit_history()

    def test_commit_history_wraps_git_failure_as_operation_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalGitRepository(_REPO_URL)
            repository.temp_dir = directory
            underlying = subprocess.CalledProcessError(
                returncode=128,
                cmd=["git", "log"],
                stderr=f"fatal: bad repository '{_REPO_URL}'",
            )
            with (
                patch(
                    "backend.app.integrations.git_repository.subprocess.run",
                    side_effect=underlying,
                ),
                self.assertRaises(GitOperationError) as exc_info,
            ):
                repository.commit_history()

        message = str(exc_info.exception)
        self.assertNotIn(_REPO_URL, message)

    def test_commit_history_rejects_invalid_max_commits(self) -> None:
        repository = LocalGitRepository(_REPO_URL)
        repository.temp_dir = "ignored"
        for value in (0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                repository.commit_history(max_commits=value)
        for value in (True, 1.5, "10"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                repository.commit_history(max_commits=value)

    @unittest.skipUnless(shutil.which("git"), "git binary not available")
    def test_commit_history_reads_real_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo_with_commits(root)

            repository = LocalGitRepository(_REPO_URL)
            repository.temp_dir = directory
            facts = repository.commit_history(
                since=datetime(2026, 1, 1, tzinfo=UTC),
                max_commits=10,
            )

        self.assertEqual(len(facts), 3)
        dates = [fact.authored_at for fact in facts]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertTrue(all(date.tzinfo is not None for date in dates))
        self.assertEqual(dates[0].date().isoformat(), "2026-09-15")


def _init_repo_with_commits(root: Path) -> None:
    """Создаёт локальный git-репозиторий с тремя пустыми коммитами."""
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for day in ("2026-09-01T10:00:00+00:00", "2026-09-08T10:00:00+00:00", "2026-09-15T10:00:00+00:00"):
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": day,
            "GIT_COMMITTER_DATE": day,
        }
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--allow-empty",
                "-q",
                "-m",
                f"commit {day}",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            env=env,
        )