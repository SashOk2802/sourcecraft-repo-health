"""Чтение дат коммитов из локального git без сети и без содержимого файлов."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.integrations.git_repository import GitCloneError, read_commit_timestamps


def commit(repo: Path, message: str, moment: datetime) -> None:
    stamp = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    env = os.environ | {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    (repo / "file.txt").write_text(message + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", "file.txt"], check=True, env=env)
    subprocess.run(["git", "-C", repo, "commit", "-m", message], check=True, env=env)


class CommitHistoryTest(unittest.TestCase):
    def test_shallow_history_keeps_only_the_requested_window(self) -> None:
        since = datetime(2026, 8, 1, tzinfo=UTC)
        until = datetime(2026, 9, 20, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "src"
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "main", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.email", "dev@example.com"], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.name", "Dev"], check=True)
            commit(repo, "old", datetime(2026, 1, 1, tzinfo=UTC))
            commit(repo, "inside", datetime(2026, 9, 1, 12, tzinfo=UTC))
            commit(repo, "later", datetime(2026, 10, 1, tzinfo=UTC))

            page = read_commit_timestamps(str(repo), since=since, until=until, auth_token="secret-token")

        self.assertFalse(page.truncated)
        self.assertEqual(page.committed_at, (datetime(2026, 9, 1, 12, tzinfo=UTC),))

    def test_budget_marks_the_page_truncated(self) -> None:
        since = datetime(2026, 1, 1, tzinfo=UTC)
        until = datetime(2026, 4, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "src"
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "main", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.email", "dev@example.com"], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.name", "Dev"], check=True)
            for index in range(3):
                commit(repo, f"c{index}", datetime(2026, 2, 1, tzinfo=UTC) + timedelta(days=index))

            page = read_commit_timestamps(str(repo), since=since, until=until, max_commits=2)

        self.assertTrue(page.truncated)
        self.assertEqual(len(page.committed_at), 2)

    def test_missing_repository_does_not_leak_the_token(self) -> None:
        missing = "/tmp/repo-health-missing-history"
        token = "super-secret-token"
        with self.assertRaises(GitCloneError) as caught:
            read_commit_timestamps(
                missing,
                since=datetime(2026, 1, 1, tzinfo=UTC),
                until=datetime(2026, 2, 1, tzinfo=UTC),
                auth_token=token,
                timeout_seconds=5,
            )

        self.assertNotIn(token, str(caught.exception))
        self.assertNotIn(missing, str(caught.exception))
