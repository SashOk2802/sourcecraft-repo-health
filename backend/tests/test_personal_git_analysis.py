from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import time
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
CLONE_URL = "https://git@git.sourcecraft.dev/sample-org/sample-private.git"


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
            "AUTHORIZATION: Basic "
            + base64.b64encode(f"git:{PERSONAL_PAT}".encode()).decode("ascii"),
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

    def test_default_git_checkout_limits_satisfy_large_repository_specification(self) -> None:
        limits = GitCheckoutLimits()
        # По разделу 9.2 ТЗ крупный репозиторий: >= 10 000 файлов, >= 500 МБ рабочей копии
        self.assertGreaterEqual(limits.max_files, 10_000)
        self.assertGreaterEqual(limits.max_tree_bytes, 500 * 1024 * 1024)
        self.assertGreaterEqual(limits.max_checkout_bytes, 500 * 1024 * 1024)

    def test_workspace_limit_stops_git_while_download_is_still_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalGitRepository(CLONE_URL, limits=GitCheckoutLimits())
            repository.temp_dir = directory
            process = GrowingWorkspaceProcess(Path(directory))

            with (
                patch(
                    "backend.app.integrations.git_repository.subprocess.Popen",
                    return_value=process,
                ),
                self.assertRaisesRegex(GitCloneError, "workspace превышает лимит"),
            ):
                repository._run_git(
                    ["clone", "--no-checkout", CLONE_URL, directory],
                    workspace_limit_bytes=8,
                )

            self.assertTrue(process.killed)
            self.assertFalse(process.completed_before_kill)

    def test_windows_workspace_limit_uses_process_group_and_tree_kill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalGitRepository(CLONE_URL, limits=GitCheckoutLimits())
            repository.temp_dir = directory
            process = WindowsGrowingWorkspaceProcess(Path(directory))

            def terminate_tree(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                process.kill()
                return subprocess.CompletedProcess([], 0, "", "")

            with (
                patch("backend.app.integrations.git_repository.os.name", "nt"),
                patch(
                    "backend.app.integrations.git_repository.subprocess.Popen",
                    return_value=process,
                ) as git_start,
                patch(
                    "backend.app.integrations.git_repository.subprocess.run",
                    side_effect=terminate_tree,
                ) as tree_kill,
                self.assertRaisesRegex(GitCloneError, "workspace превышает лимит"),
            ):
                repository._run_git(
                    ["clone", "--no-checkout", CLONE_URL, directory],
                    workspace_limit_bytes=8,
                )

            creation_flags = git_start.call_args.kwargs["creationflags"]
            self.assertEqual(creation_flags, 0x00000200)
            tree_kill.assert_called_once_with(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
            )
            self.assertTrue(process.killed)

    @unittest.skipUnless(os.name == "nt", "Windows process-tree integration test")
    def test_windows_tree_kill_stops_real_child_writer(self) -> None:
        child_code = """
import pathlib
import sys
import time

target = pathlib.Path(sys.argv[1])
while True:
    with target.open("ab") as output:
        output.write(b"x" * 4096)
    time.sleep(0.005)
"""
        parent_code = """
import subprocess
import sys
import time

subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2]])
time.sleep(30)
"""
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            output = workspace / "child-output.bin"
            repository = LocalGitRepository(CLONE_URL, timeout_seconds=5)
            repository.temp_dir = directory

            with self.assertRaisesRegex(GitCloneError, "workspace превышает лимит"):
                repository._run_git_with_workspace_limit(
                    [sys.executable, "-c", parent_code, child_code, str(output)],
                    env=os.environ.copy(),
                    max_bytes=32 * 1024,
                )

            size_after_kill = output.stat().st_size
            time.sleep(0.2)
            self.assertEqual(output.stat().st_size, size_after_kill)

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


class GrowingWorkspaceProcess:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._grew = False
        self.killed = False
        self.completed_before_kill = False
        self._return_code: int | None = None

    def poll(self) -> int | None:
        if not self._grew:
            pack = self._workspace / ".git" / "objects" / "pack"
            pack.mkdir(parents=True)
            (pack / "incoming.pack").write_bytes(b"x" * 9)
            self._grew = True
        return self._return_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if not self.killed:
            self.completed_before_kill = True
            self._return_code = 0
        return self._return_code or 0

    def kill(self) -> None:
        self.killed = True
        self._return_code = -9


class WindowsGrowingWorkspaceProcess(GrowingWorkspaceProcess):
    pid = 4242
