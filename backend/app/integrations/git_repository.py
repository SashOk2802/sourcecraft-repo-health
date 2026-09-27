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
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SOURCECRAFT_GIT_HOST = "git.sourcecraft.dev"
DEFAULT_GIT_TIMEOUT_SECONDS = 60.0
MAX_FILE_BYTES = 512 * 1024
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
_REGULAR_GIT_FILE_MODES = frozenset({"100644", "100755"})


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
        # После blobless clone здесь хранится commit и метаданные обычных
        # файлов. Рабочее дерево намеренно не checkout'ится: иначе Git скачает
        # все blob'ы репозитория ещё до лимитов анализатора.
        self._treeish: str | None = None
        self._tree_entries: dict[str, int | None] | None = None

    def clone(self) -> str:
        """Получает только Git-дерево без checkout всех blob'ов.

        ``--filter=blob:none`` оставляет на диске commit и дерево путей, но не
        содержимое файлов. Размер каждого blob'а проверяется по метаданным до
        отдельного чтения в ``read_file``; поэтому крупный репозиторий не может
        заполнить диск до срабатывания бюджета Code health.
        """

        self.cleanup()
        self.temp_dir = tempfile.mkdtemp(prefix="repo-health-")
        try:
            self._run_git(
                [
                    "clone",
                    "--depth",
                    "1",
                    "--filter=blob:none",
                    "--no-checkout",
                    self._repo_url,
                    self.temp_dir,
                ]
            )
            if self._ref is not None:
                self._run_git(
                    [
                        "-C",
                        self.temp_dir,
                        "fetch",
                        "--depth",
                        "1",
                        "--filter=blob:none",
                        "origin",
                        self._ref,
                    ]
                )
            self._treeish = self._resolve_commit("FETCH_HEAD" if self._ref is not None else "HEAD")
            self._tree_entries = self._list_tree(self._treeish)
            return self.temp_dir
        except (GitCloneError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            self.cleanup()
            if isinstance(error, subprocess.TimeoutExpired):
                raise GitCloneError("Клонирование репозитория превысило лимит времени.") from error
            if isinstance(error, GitCloneError):
                raise
            raise GitCloneError("Не удалось подготовить содержимое репозитория.") from error

    def file_exists(self, relative_path: str) -> bool:
        """Проверяет обычный файл внутри временного дерева, не следуя симлинкам."""

        if self._tree_entries is not None:
            return relative_path in self._tree_entries
        path = self._resolve_file(relative_path)
        return path is not None and path.is_file() and not path.is_symlink()

    def read_file(self, relative_path: str, *, max_bytes: int = MAX_FILE_BYTES) -> str | None:
        """Читает текстовый файл с лимитом размера; выход из дерева запрещён."""

        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if self._tree_entries is not None:
            size = self._tree_entries.get(relative_path)
            if self._treeish is None or size is None or size > max_bytes:
                return None
            try:
                content = self._run_git_bytes(
                    ["-C", self._require_temp_dir(), "cat-file", "blob", f"{self._treeish}:{relative_path}"]
                ).stdout
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                return None
            # Git должен вернуть ровно размер, объявленный деревом. Повторная
            # проверка защищает бюджет при изменении удалённого объекта.
            if len(content) > max_bytes or len(content) != size:
                return None
            return content.decode("utf-8", errors="replace")
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

        if self._tree_entries is not None:
            for relative_path in sorted(self._tree_entries):
                if any(part in excluded_directories for part in PurePosixPath(relative_path).parts[:-1]):
                    continue
                yield relative_path
            return
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
        if self._tree_entries is not None:
            return self._tree_entries.get(relative_path)
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
        self._treeish = None
        self._tree_entries = None
        if temp_dir is None:
            return
        try:
            shutil.rmtree(temp_dir, onerror=_remove_readonly)
        except OSError:
            logger.warning("Не удалось удалить временную папку Git-клона.")

    def _run_git(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        environment = self._git_environment()
        # Backend-процесс не должен ожидать ввода логина или пароля в терминале.
        environment["GIT_TERMINAL_PROMPT"] = "0"
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

    def _run_git_bytes(self, arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
        """Выполняет Git без вывода ошибок и возвращает только blob bytes."""

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
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=self._timeout_seconds,
            env=self._git_environment(),
        )

    def _git_environment(self) -> dict[str, str]:
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
        return environment

    def _resolve_commit(self, reference: str) -> str:
        completed = self._run_git_bytes(
            ["-C", self._require_temp_dir(), "rev-parse", "--verify", f"{reference}^{{commit}}"]
        )
        commit = completed.stdout.decode("ascii", errors="strict").strip()
        if not _COMMIT_SHA.fullmatch(commit):
            raise GitCloneError("Не удалось подтвердить commit подготовленного репозитория.")
        return commit

    def _list_tree(self, treeish: str) -> dict[str, int | None]:
        """Возвращает метаданные только обычных файлов без чтения blob'ов."""

        completed = self._run_git_bytes(
            ["-C", self._require_temp_dir(), "ls-tree", "-r", "-z", "-l", treeish]
        )
        entries: dict[str, int | None] = {}
        for record in completed.stdout.split(b"\0"):
            if not record:
                continue
            metadata, separator, raw_path = record.partition(b"\t")
            fields = metadata.split()
            if separator != b"\t" or len(fields) != 4:
                raise GitCloneError("Git вернул некорректное дерево репозитория.")
            mode, object_type, _object_id, raw_size = fields
            if mode.decode("ascii", errors="ignore") not in _REGULAR_GIT_FILE_MODES or object_type != b"blob":
                continue
            relative_path = raw_path.decode("utf-8", errors="surrogateescape")
            path = PurePosixPath(relative_path)
            if not relative_path or path.is_absolute() or ".." in path.parts:
                raise GitCloneError("Git вернул небезопасный путь в дереве репозитория.")
            try:
                size = int(raw_size) if raw_size != b"-" else None
            except ValueError as error:
                raise GitCloneError("Git вернул некорректный размер файла.") from error
            if size is not None and size < 0:
                raise GitCloneError("Git вернул некорректный размер файла.")
            entries[relative_path] = size
        return entries

    def _require_temp_dir(self) -> str:
        if self.temp_dir is None:
            raise GitCloneError("Git-репозиторий ещё не подготовлен.")
        return self.temp_dir

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
