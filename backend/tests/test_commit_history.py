"""Чтение дат коммитов из локального git без сети и без содержимого файлов."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.app.integrations.git_repository import GitCloneError, read_commit_timestamps


def commit(repo: Path, message: str, moment: datetime) -> str:
    stamp = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    env = os.environ | {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    (repo / "file.txt").write_text(message + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", "file.txt"], check=True, env=env)
    subprocess.run(["git", "-C", repo, "commit", "-m", message], check=True, env=env)
    return subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()


def init_repo(root: Path) -> Path:
    repo = root / "src"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "dev@example.com"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "Dev"], check=True)
    return repo


class CommitHistoryTest(unittest.TestCase):
    def test_shallow_history_keeps_only_the_requested_window(self) -> None:
        since = datetime(2026, 8, 1, tzinfo=UTC)
        until = datetime(2026, 9, 20, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            repo = init_repo(Path(temporary))
            commit(repo, "old", datetime(2026, 1, 1, tzinfo=UTC))
            commit(repo, "inside", datetime(2026, 9, 1, 12, tzinfo=UTC))
            head = commit(repo, "later", datetime(2026, 10, 1, tzinfo=UTC))

            page = read_commit_timestamps(
                str(repo),
                since=since,
                until=until,
                revision=head,
                auth_token="secret-token",
            )

        self.assertFalse(page.truncated)
        self.assertEqual(page.committed_at, (datetime(2026, 9, 1, 12, tzinfo=UTC),))

    def test_budget_marks_the_page_truncated(self) -> None:
        since = datetime(2026, 1, 1, tzinfo=UTC)
        until = datetime(2026, 4, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            repo = init_repo(Path(temporary))
            head = ""
            for index in range(3):
                head = commit(repo, f"c{index}", datetime(2026, 2, 1, tzinfo=UTC) + timedelta(days=index))

            page = read_commit_timestamps(
                str(repo), since=since, until=until, revision=head, max_commits=2
            )

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
                revision="a" * 40,
                auth_token=token,
                timeout_seconds=5,
            )

        self.assertNotIn(token, str(caught.exception))
        self.assertNotIn(missing, str(caught.exception))

    def test_commit_exactly_at_period_start_is_included(self) -> None:
        since = datetime(2026, 8, 1, 12, tzinfo=UTC)
        until = datetime(2026, 9, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            repo = init_repo(Path(temporary))
            commit(repo, "before", since - timedelta(seconds=1))
            head = commit(repo, "boundary", since)

            page = read_commit_timestamps(str(repo), since=since, until=until, revision=head)

        self.assertEqual(page.committed_at, (since,))

    def test_log_stops_at_the_analyzed_revision(self) -> None:
        since = datetime(2026, 8, 1, tzinfo=UTC)
        until = datetime(2026, 10, 1, tzinfo=UTC)
        analyzed_at = datetime(2026, 9, 1, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            repo = init_repo(Path(temporary))
            analyzed = commit(repo, "analyzed", analyzed_at)
            commit(repo, "after-analysis", datetime(2026, 9, 15, 12, tzinfo=UTC))

            page = read_commit_timestamps(str(repo), since=since, until=until, revision=analyzed)

        self.assertEqual(page.committed_at, (analyzed_at,))

    def test_revision_on_another_branch_is_fetched(self) -> None:
        since = datetime(2026, 8, 1, tzinfo=UTC)
        until = datetime(2026, 10, 1, tzinfo=UTC)
        feature_at = datetime(2026, 9, 2, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            repo = init_repo(Path(temporary))
            commit(repo, "base", datetime(2026, 1, 1, tzinfo=UTC))
            subprocess.run(["git", "-C", repo, "checkout", "-b", "feature"], check=True)
            feature = commit(repo, "feature", feature_at)
            subprocess.run(["git", "-C", repo, "checkout", "main"], check=True)
            commit(repo, "main-later", datetime(2026, 9, 10, 12, tzinfo=UTC))

            page = read_commit_timestamps(str(repo), since=since, until=until, revision=feature)

        self.assertEqual(page.committed_at, (feature_at,))

    def test_revision_older_than_the_window_is_empty(self) -> None:
        since = datetime(2026, 8, 1, tzinfo=UTC)
        until = datetime(2026, 10, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            repo = init_repo(Path(temporary))
            old = commit(repo, "old", datetime(2026, 1, 1, tzinfo=UTC))
            commit(repo, "later", datetime(2026, 9, 1, 12, tzinfo=UTC))

            page = read_commit_timestamps(str(repo), since=since, until=until, revision=old)

        self.assertFalse(page.truncated)
        self.assertEqual(page.committed_at, ())

    def test_token_is_rejected_for_an_untrusted_git_host_before_git_starts(self) -> None:
        with patch("backend.app.integrations.git_repository.subprocess.run") as git_run:
            with self.assertRaises(GitCloneError):
                read_commit_timestamps(
                    "https://attacker.example/organization/repository.git",
                    since=datetime(2026, 8, 1, tzinfo=UTC),
                    until=datetime(2026, 9, 1, tzinfo=UTC),
                    revision="a" * 40,
                    auth_token="secret-token",
                )

        git_run.assert_not_called()

    def test_symbolic_or_partial_revision_is_rejected_before_git_starts(self) -> None:
        for revision in ("main", "a" * 39, "--upload-pack=malicious"):
            with self.subTest(revision=revision), patch(
                "backend.app.integrations.git_repository.subprocess.run"
            ) as git_run:
                with self.assertRaises(ValueError):
                    read_commit_timestamps(
                        "/tmp/repo-health-history",
                        since=datetime(2026, 8, 1, tzinfo=UTC),
                        until=datetime(2026, 9, 1, tzinfo=UTC),
                        revision=revision,
                    )

            git_run.assert_not_called()

    def test_commit_one_second_after_period_end_is_not_returned(self) -> None:
        since = datetime(2026, 8, 1, tzinfo=UTC)
        until = datetime(2026, 9, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            repo = init_repo(Path(temporary))
            commit(repo, "period-end", until)
            head = commit(repo, "after-period", until + timedelta(seconds=1))

            page = read_commit_timestamps(str(repo), since=since, until=until, revision=head)

        self.assertEqual(page.committed_at, (until,))

    def test_clone_and_log_share_one_timeout_budget(self) -> None:
        since = datetime(2026, 8, 1, tzinfo=UTC)
        until = datetime(2026, 10, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            repo = init_repo(Path(temporary))
            head = commit(repo, "inside", datetime(2026, 9, 1, 12, tzinfo=UTC))
            recorded: list[float] = []
            real_run = subprocess.run

            def spy(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
                timeout = kwargs.get("timeout")
                if isinstance(timeout, (int, float)):
                    recorded.append(float(timeout))
                return real_run(*args, **kwargs)  # type: ignore[arg-type]

            with patch("backend.app.integrations.git_repository.subprocess.run", spy):
                read_commit_timestamps(
                    str(repo),
                    since=since,
                    until=until,
                    revision=head,
                    timeout_seconds=30,
                )

        self.assertGreaterEqual(len(recorded), 2)
        self.assertLessEqual(recorded[0], 30)
        self.assertLess(recorded[-1], recorded[0])
        self.assertTrue(all(item <= 30 for item in recorded))
