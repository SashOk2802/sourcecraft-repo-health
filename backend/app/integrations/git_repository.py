"""Безопасная временная Git-рабочая область для анализа файлов SourceCraft."""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from base64 import b64encode
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SOURCECRAFT_GIT_HOST = "git.sourcecraft.dev"
DEFAULT_GIT_TIMEOUT_SECONDS = 60.0
MAX_FILE_BYTES = 512 * 1024
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")


class GitCloneError(RuntimeError):
    """Ошибка подготовки рабочего дерева без URL, токена и stderr Git."""


def sourcecraft_clone_url(organization_slug: str, repository_slug: str) -> str:
    """Строит допустимый HTTPS URL SourceCraft из двух slug.

    ``web_url`` репозитория не используется: это отображаемая ссылка, а не
    доверенный адрес, на который разрешено посылать учетные данные Git.
    """

    for value, name in (
        (organization_slug, "organization_slug"),
        (repository_slug, "repository_slug"),
    ):
        if not isinstance(value, str) or not _SEGMENT.fullmatch(value):
            raise ValueError(f"SourceCraft {name} must be one URL path segment")
    return f"https://git@{SOURCECRAFT_GIT_HOST}/{organization_slug}/{repository_slug}.git"


class LocalGitRepository:
    """Клонирует доверенный SourceCraft-репозиторий во временную папку."""

    def __init__(
        self,
        repo_url: str,
        ref: str | None = None,
        *,
        auth_token: str | None = None,
        timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    ) -> None:
        _validate_clone_url(repo_url)
        if ref is not None and (not isinstance(ref, str) or not _COMMIT_SHA.fullmatch(ref)):
            raise ValueError("repository ref must be a commit SHA")
        if timeout_seconds <= 0:
            raise ValueError("git timeout must be positive")
        self._repo_url = repo_url
        self._ref = ref
        self._auth_token = auth_token or None
        self._timeout_seconds = timeout_seconds
        self.temp_dir: str | None = None

    def clone(self) -> str:
        """Клонирует дерево и, если задан SHA, переключается на него."""

        self.cleanup()
        self.temp_dir = tempfile.mkdtemp(prefix="repo-health-")
        try:
            self._run_git(["clone", "--depth", "1", self._repo_url, self.temp_dir])
            if self._ref is not None:
                self._run_git(["-C", self.temp_dir, "fetch", "--depth", "1", "origin", self._ref])
                self._run_git(["-C", self.temp_dir, "checkout", "--detach", "FETCH_HEAD"])
            return self.temp_dir
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            self.cleanup()
            if isinstance(error, subprocess.TimeoutExpired):
                raise GitCloneError("Клонирование репозитория превысило лимит времени.") from error
            raise GitCloneError("Не удалось подготовить содержимое репозитория.") from error

    def file_exists(self, relative_path: str) -> bool:
        """Проверяет обычный файл внутри временного дерева, не следуя симлинкам."""

        path = self._resolve_file(relative_path)
        return path is not None and path.is_file() and not path.is_symlink()

    def read_file(self, relative_path: str, *, max_bytes: int = MAX_FILE_BYTES) -> str | None:
        """Читает текстовый файл с лимитом размера; выход из дерева запрещён."""

        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        path = self._resolve_file(relative_path)
        if path is None or not path.is_file() or path.is_symlink():
            return None
        try:
            with path.open("rb") as file:
                return file.read(max_bytes).decode("utf-8", errors="replace")
        except OSError:
            return None

    def iter_files(self, *, excluded_directories: frozenset[str]) -> Iterator[str]:
        """Возвращает обычные файлы, не покидая workspace."""

        if self.temp_dir is None:
            return
        root = Path(self.temp_dir).resolve()
        for current_root, directories, filenames in os.walk(root, followlinks=False):
            directories[:] = sorted(
                directory
                for directory in directories
                if directory not in excluded_directories
                and not Path(current_root, directory).is_symlink()
            )
            for filename in sorted(filenames):
                candidate = Path(current_root, filename)
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                try:
                    yield str(candidate.resolve().relative_to(root))
                except ValueError:
                    continue

    def file_size(self, relative_path: str) -> int | None:
        path = self._resolve_file(relative_path)
        if path is None or path.is_symlink():
            return None
        try:
            return path.stat().st_size
        except OSError:
            return None

    def cleanup(self) -> None:
        """Удаляет только созданную этим объектом временную рабочую папку."""

        temp_dir, self.temp_dir = self.temp_dir, None
        if temp_dir is None:
            return
        try:
            shutil.rmtree(temp_dir, onerror=_remove_readonly)
        except OSError:
            logger.warning("Не удалось удалить временную папку Git-клона.")

    def _run_git(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        # Backend-процесс не должен ожидать ввода логина или пароля в терминале.
        environment["GIT_TERMINAL_PROMPT"] = "0"
        if self._auth_token is not None:
            # Для Git SourceCraft PAT является паролем HTTP Basic. Заголовок
            # передаётся через окружение и не попадает в URL или argv процесса.
            credentials = b64encode(f"git:{self._auth_token}".encode()).decode("ascii")
            environment["GIT_CONFIG_COUNT"] = "1"
            environment["GIT_CONFIG_KEY_0"] = (
                f"http.https://{SOURCECRAFT_GIT_HOST}/.extraHeader"
            )
            environment["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {credentials}"
        return subprocess.run(
            [
                "git",
                "-c",
                "protocol.file.allow=never",
                "-c",
                "protocol.ext.allow=never",
                "-c",
                "http.followRedirects=false",
                *arguments,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=self._timeout_seconds,
            env=environment,
        )

    def _resolve_file(self, relative_path: str) -> Path | None:
        if self.temp_dir is None or not isinstance(relative_path, str):
            return None
        root = Path(self.temp_dir).resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate


def _validate_clone_url(repo_url: str) -> None:
    parsed = urlsplit(repo_url)
    path_segments = parsed.path.split("/")
    valid_path = (
        len(path_segments) == 3
        and not path_segments[0]
        and _SEGMENT.fullmatch(path_segments[1]) is not None
        and path_segments[2].endswith(".git")
        and _SEGMENT.fullmatch(path_segments[2][:-4]) is not None
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname != SOURCECRAFT_GIT_HOST
        or parsed.port not in (None, 443)
        or parsed.username != "git"
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not valid_path
    ):
        raise ValueError("repository URL must use the official SourceCraft Git HTTPS host")


def _remove_readonly(function: object, path: str, _: object) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    if callable(function):
        function(path)
