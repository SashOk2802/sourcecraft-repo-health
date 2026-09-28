from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from backend.app.contracts import RepositoryRef
from backend.app.identity import (
    InMemorySourceCraftConnectionStore,
    SourceCraftConnectionService,
    SourceCraftConnectionUnavailableError,
    SourceCraftTokenVault,
)
from backend.app.integrations.git_repository import (
    GitCheckoutLimits,
    GitCloneError,
    LocalGitRepository,
)

OWNER = "user-owner"
OTHER_USER = "user-other"
PERSONAL_PAT = "synthetic-personal-pat"
COMMIT_SHA = "a" * 40
CLONE_URL = "https://sourcecraft.dev/sample-org/sample-private.git"


class PersonalGitCredentialTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = InMemorySourceCraftConnectionStore()
        self.connections = SourceCraftConnectionService(
            SourceCraftTokenVault(Fernet.generate_key().decode("ascii")),
            self.store,
            sourcecraft_client_factory=lambda token: ProfileClient(token),  # type: ignore[arg-type]
            clock=lambda: datetime(2026, 9, 28, 12, tzinfo=UTC),
        )
        await self.connections.connect(OWNER, PERSONAL_PAT)

    async def test_prepared_workspace_releases_pat_and_cleanup_removes_files(self) -> None:
        lease = await self.connections.issue_lease(OWNER)
        assert lease is not None
        observed_tokens: list[str | None] = []

        def clone_without_network(repository: LocalGitRepository, _: str | None) -> None:
            observed_tokens.append(repository.auth_token)
            assert repository.temp_dir is not None
            (Path(repository.temp_dir) / "README.md").write_text("# Example\n", encoding="utf-8")

        with patch.object(LocalGitRepository, "_clone_bounded", clone_without_network):
            workspace = self.connections.prepare_git_repository(
                lease,
                OWNER,
                RepositoryRef(
                    "repo-private",
                    "sample-org",
                    "sample-private",
                    "https://sourcecraft.dev/sample-org/sample-private",
                ),
                COMMIT_SHA,
            )

        directory = workspace.temp_dir
        self.assertEqual(observed_tokens, [PERSONAL_PAT])
        self.assertIsNone(workspace.auth_token)
        self.assertNotIn(PERSONAL_PAT, repr(workspace))
        assert directory is not None
        self.assertTrue(Path(directory).exists())

        workspace.cleanup()

        self.assertFalse(Path(directory).exists())

    async def test_wrong_owner_is_rejected_before_git_receives_pat(self) -> None:
        lease = await self.connections.issue_lease(OWNER)
        assert lease is not None

        with (
            patch.object(LocalGitRepository, "clone") as clone,
            self.assertRaises(PermissionError),
        ):
            self.connections.prepare_git_repository(
                lease,
                OTHER_USER,
                RepositoryRef("repo-private", "sample-org", "sample-private"),
                COMMIT_SHA,
            )

        clone.assert_not_called()

    async def test_git_failure_does_not_expose_pat_or_private_url(self) -> None:
        lease = await self.connections.issue_lease(OWNER)
        assert lease is not None

        def fail(repository: LocalGitRepository, _: str | None) -> None:
            raise GitCloneError(f"failed {repository.repo_url} with {repository.auth_token}")

        with (
            patch.object(LocalGitRepository, "_clone_bounded", fail),
            self.assertRaises(SourceCraftConnectionUnavailableError) as raised,
        ):
            self.connections.prepare_git_repository(
                lease,
                OWNER,
                RepositoryRef(
                    "repo-private",
                    "sample-org",
                    "sample-private",
                    "https://sourcecraft.dev/sample-org/sample-private",
                ),
                COMMIT_SHA,
            )

        rendered = str(raised.exception)
        self.assertNotIn(PERSONAL_PAT, rendered)
        self.assertNotIn(CLONE_URL, rendered)


class BoundedGitRepositoryTest(unittest.TestCase):
    def test_bounded_checkout_materializes_exact_commit_and_cleans_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory:
            source = Path(source_directory)
            subprocess.run(["git", "init", "-b", "main", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "dev@example.test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "Developer"],
                check=True,
            )
            (source / "README.md").write_text("# Example\n", encoding="utf-8")
            (source / "service.py").write_text("# TODO: example\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(source), "commit", "-m", "example"],
                check=True,
            )
            commit_sha = subprocess.run(
                ["git", "-C", str(source), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            repository = LocalGitRepository(
                source.resolve().as_uri(),
                ref=commit_sha,
                limits=GitCheckoutLimits(),
            )

            directory = repository.clone()

            self.assertEqual(repository.read_file("README.md"), "# Example\n")
            self.assertEqual(repository.read_file("service.py"), "# TODO: example\n")
            repository.cleanup()
            self.assertFalse(Path(directory).exists())

    def test_pat_is_only_in_clone_environment_and_is_removed_after_clone(self) -> None:
        repository = LocalGitRepository(CLONE_URL, auth_token=PERSONAL_PAT)

        with patch(
            "backend.app.integrations.git_repository.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as git_run:
            repository._run_git(["clone", "--depth", "1", CLONE_URL, "workspace"])
            repository._run_git(["-C", "workspace", "checkout", "HEAD"])

        clone_call, checkout_call = git_run.call_args_list
        self.assertNotIn(PERSONAL_PAT, repr(clone_call.args[0]))
        self.assertEqual(
            clone_call.kwargs["env"]["GIT_CONFIG_VALUE_0"],
            f"AUTHORIZATION: Bearer {PERSONAL_PAT}",
        )
        self.assertNotIn("GIT_CONFIG_VALUE_0", checkout_call.kwargs["env"])

        with patch.object(repository, "_clone_shallow_with_ref", return_value=None):
            repository.ref = COMMIT_SHA
            directory = repository.clone()

        self.assertIsNone(repository.auth_token)
        repository.cleanup()
        self.assertFalse(Path(directory).exists())

    def test_tree_file_blob_and_total_size_limits_fail_closed(self) -> None:
        cases = (
            (
                GitCheckoutLimits(max_files=1, max_blob_bytes=10, max_tree_bytes=20),
                ["blob 5\n", "blob 5\n"],
                "числа файлов",
            ),
            (
                GitCheckoutLimits(max_files=2, max_blob_bytes=4, max_tree_bytes=20),
                ["blob 5\n"],
                "слишком большой файл",
            ),
            (
                GitCheckoutLimits(max_files=2, max_blob_bytes=10, max_tree_bytes=8),
                ["blob 5\n", "blob 5\n"],
                "общего размера",
            ),
        )
        for limits, output, expected in cases:
            with self.subTest(expected=expected):
                repository = LocalGitRepository(CLONE_URL, limits=limits)
                repository.temp_dir = "prepared-workspace"
                process = FakeProcess(output)
                with (
                    patch(
                        "backend.app.integrations.git_repository.subprocess.Popen",
                        return_value=process,
                    ),
                    self.assertRaisesRegex(GitCloneError, expected),
                ):
                    repository._validate_tree("FETCH_HEAD", limits)
                self.assertTrue(process.killed)

    def test_authenticated_git_log_never_contains_pat_or_private_url(self) -> None:
        repository = LocalGitRepository(CLONE_URL, auth_token=PERSONAL_PAT)
        failure = subprocess.CalledProcessError(
            128,
            ["git", "clone"],
            stderr=f"failed for {CLONE_URL} using {PERSONAL_PAT}",
        )

        with (
            patch(
                "backend.app.integrations.git_repository.subprocess.run",
                side_effect=failure,
            ),
            self.assertLogs("backend.app.integrations.git_repository", level="DEBUG") as logs,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            repository._run_git(["clone", CLONE_URL, "workspace"])

        rendered = "\n".join(logs.output)
        self.assertNotIn(PERSONAL_PAT, rendered)
        self.assertNotIn(CLONE_URL, rendered)


class ProfileClient:
    def __init__(self, token: str) -> None:
        self._token = token

    def get_json(self, path: str) -> dict[str, str]:
        if path != "/user":
            raise AssertionError(path)
        return {"username": "owner"}

    def close(self) -> None:
        return None


class FakeProcess:
    def __init__(self, output: list[str]) -> None:
        self.stdout = iter(output)
        self.killed = False
        self._return_code: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._return_code = -9 if self.killed else 0
        return self._return_code

    def poll(self) -> int | None:
        return self._return_code

    def kill(self) -> None:
        self.killed = True
