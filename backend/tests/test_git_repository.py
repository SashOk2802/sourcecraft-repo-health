"""Тесты интеграционного слоя LocalGitRepository: клонирование, безопасность путей."""

import os
import subprocess
from unittest.mock import patch

import pytest

from backend.app.integrations.git_repository import (
    GitCloneError,
    LocalGitRepository,
)

# --- T.1: clone выполняется с ожидаемым URL и Bearer-токеном -----------------


def test_clone_uses_resolved_url_and_bearer_credential():
    """Проверяет фактический вызов git clone с URL и токеном (T.1)."""
    url = "https://sourcecraft.dev/org/repo.git"
    token = "secret-token"

    with patch("backend.app.integrations.git_repository.subprocess.run") as mock_run:
        repo = LocalGitRepository(repo_url=url, ref="main", auth_token=token)
        repo.clone()

    first_call = mock_run.call_args.args[0]
    assert first_call[0] == "git"
    assert "clone" in first_call
    assert url in first_call
    assert "-c" in first_call
    header = first_call[first_call.index("-c") + 1]
    assert header == f"http.extraheader=AUTHORIZATION: Bearer {token}"


def test_run_git_passes_timeout_and_captures_output():
    """Таймаут и capture_output передаются в subprocess.run (I.1)."""
    with patch("backend.app.integrations.git_repository.subprocess.run") as mock_run:
        repo = LocalGitRepository(repo_url="https://example/repo.git", timeout_seconds=7.5)
        repo._run_git(["status"])

    kwargs = mock_run.call_args.kwargs
    assert kwargs["timeout"] == 7.5
    assert kwargs["capture_output"] is True


# --- T.2: fallback при SHA-рефе ---------------------------------------------


def test_fallback_to_default_branch_when_ref_is_commit_sha():
    """Ветка по SHA не клонируется → дефолтная ветка + fetch по хешу (T.2, 2.3)."""
    url = "https://example/repo.git"
    sha = "a" * 40
    commands: list[list[str]] = []

    def fake_run(arguments: list[str], **kwargs):
        commands.append(arguments)
        if "clone" in arguments and "--branch" in arguments:
            raise subprocess.CalledProcessError(128, arguments)
        return subprocess.CompletedProcess(arguments, 0)

    with patch(
        "backend.app.integrations.git_repository.subprocess.run", side_effect=fake_run
    ):
        repo = LocalGitRepository(repo_url=url, ref=sha)
        repo.clone()

    clones = [command for command in commands if command[1] == "clone"]
    assert len(clones) == 2, "первый clone --branch падает, второй — дефолтная ветка"
    assert "--branch" in clones[0]
    assert "--branch" not in clones[1]

    fetches = [command for command in commands if "fetch" in command]
    assert len(fetches) == 1
    assert sha in fetches[0]

    checkouts = [command for command in commands if "checkout" in command]
    assert len(checkouts) == 1
    assert checkouts[0][-1] == "FETCH_HEAD"


# --- 2.2 / I.1: ошибки git не содержат URL и токен ---------------------------


def test_clone_error_does_not_leak_url_or_token():
    url = "https://secret-host.example/private/repo.git"
    token = "super-secret-token"
    underlying = subprocess.CalledProcessError(
        returncode=128,
        cmd=["git", "clone"],
        stderr=f"fatal: authentication failed for '{url}'",
    )

    with patch(
        "backend.app.integrations.git_repository.subprocess.run", side_effect=underlying
    ):
        repo = LocalGitRepository(repo_url=url, auth_token=token)
        with pytest.raises(GitCloneError) as exc_info:
            repo.clone()

    message = str(exc_info.value)
    assert url not in message
    assert token not in message


def test_clone_timeout_raises_clean_error_without_url():
    url = "https://secret-host.example/private/repo.git"

    with patch(
        "backend.app.integrations.git_repository.subprocess.run",
        side_effect=subprocess.TimeoutExpired(["git"], 60),
    ):
        repo = LocalGitRepository(repo_url=url, timeout_seconds=1)
        with pytest.raises(GitCloneError) as exc_info:
            repo.clone()

    assert url not in str(exc_info.value)


# --- I.2: чтение файлов только внутри временной директории -------------------


def test_read_file_rejects_escape_outside_temp(tmp_path):
    repo = LocalGitRepository(repo_url="https://example/repo.git")
    repo.temp_dir = str(tmp_path)
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    assert repo.read_file("README.md") == "hello"
    assert repo.read_file("../../etc/passwd") is None

    sibling = tmp_path.parent / "outside-secret.txt"
    sibling.write_text("secret", encoding="utf-8")
    assert repo.read_file(f"../{sibling.name}") is None
    assert repo.file_exists(f"../{sibling.name}") is False


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_read_file_rejects_symlink_out_of_tree(tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.md"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlink creation not permitted")

    repo = LocalGitRepository(repo_url="https://example/repo.git")
    repo.temp_dir = str(tmp_path)

    assert repo.read_file("link.md") is None
    assert repo.file_exists("link.md") is False


# --- I.3: сбой cleanup не пробрасывается наружу ------------------------------


def test_cleanup_failure_does_not_raise(tmp_path):
    repo = LocalGitRepository(repo_url="https://example/repo.git")
    repo.temp_dir = str(tmp_path)

    with patch(
        "backend.app.integrations.git_repository.shutil.rmtree",
        side_effect=OSError("permission denied"),
    ):
        repo.cleanup()  # не должно бросать исключение

    assert repo.temp_dir is None


def test_cleanup_is_idempotent():
    repo = LocalGitRepository(repo_url="https://example/repo.git")
    assert repo.temp_dir is None
    repo.cleanup()  # no-op, без исключений


# --- I.4: повторный clone не теряет предыдущую директорию --------------------


def test_repeated_clone_replaces_previous_temp_dir():
    with patch("backend.app.integrations.git_repository.subprocess.run"):
        repo = LocalGitRepository(repo_url="https://example/repo.git")

        first = repo.clone()
        assert os.path.isdir(first)

        second = repo.clone()
        assert os.path.isdir(second)
        assert first != second
        assert not os.path.exists(first), "старый клон должен быть убран до нового"