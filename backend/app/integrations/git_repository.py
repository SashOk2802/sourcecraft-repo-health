"""Безопасная временная Git-рабочая область для анализа файлов SourceCraft."""

from __future__ import annotations

import logging
import os
import re
import select
import shutil
import stat
import subprocess
import tempfile
import time
from base64 import b64encode
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SOURCECRAFT_GIT_HOSTS = frozenset({"api.sourcecraft.tech", "sourcecraft.dev"})
DEFAULT_SOURCECRAFT_GIT_HOST = "api.sourcecraft.tech"
DEFAULT_GIT_TIMEOUT_SECONDS = 60.0
MAX_FILE_BYTES = 512 * 1024
MAX_TREE_ENTRIES = 25_000
MAX_TREE_BYTES = 8 * 1024 * 1024
_TREE_STREAM_CHUNK_BYTES = 8 * 1024
_MAX_TREE_RECORD_BYTES = 16 * 1024
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
_REGULAR_GIT_FILE_MODES = frozenset({"100644", "100755"})


class GitCloneError(RuntimeError):
    """Ошибка подготовки рабочего дерева без URL, токена и stderr Git."""


class GitTreeLimitError(GitCloneError):
    """Метаданные Git-дерева превысили безопасный бюджет анализа."""


class GitBlobReadError(GitCloneError):
    """Не удалось получить blob, нужный для полного анализа файла."""


@dataclass(frozen=True, slots=True)
class GitTreeEntry:
    """Один regular file из Git-дерева без содержимого blob'а."""

    relative_path: str
    size: int | None


def _redact(value: str, *, repo_url: str, token: str | None) -> str:
    """Удаляет URL и токен из текста до его передачи в API-ответ."""

    redacted = value.replace(repo_url, "<repo>")
    if token:
        redacted = redacted.replace(token, "<token>")
    return redacted


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
    return (
        f"https://{DEFAULT_SOURCECRAFT_GIT_HOST}/"
        f"{organization_slug}/{repository_slug}.git"
    )


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
        clone_host = urlsplit(repo_url).hostname
        assert clone_host is not None  # Проверено _validate_clone_url выше.
        self._clone_host = clone_host
        self._ref = ref
        self._auth_token = auth_token or None
        self._timeout_seconds = timeout_seconds
        self.temp_dir: str | None = None
        # После blobless clone здесь хранится commit и метаданные обычных
        # файлов. Рабочее дерево намеренно не checkout'ится: иначе Git скачает
        # все blob'ы репозитория ещё до лимитов анализатора.
        self._treeish: str | None = None

    def redact(self, value: str) -> str:
        """Возвращает безопасный для отчёта текст ошибки Git-операции."""

        return _redact(value, repo_url=self._repo_url, token=self._auth_token)

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

        if self._treeish is not None:
            return self._tree_entry_for_path(relative_path) is not None
        path = self._resolve_file(relative_path)
        return path is not None and path.is_file() and not path.is_symlink()

    def read_file(
        self,
        relative_path: str,
        *,
        max_bytes: int = MAX_FILE_BYTES,
        expected_size: int | None = None,
    ) -> str | None:
        """Читает текстовый файл с лимитом размера; выход из дерева запрещён."""

        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if expected_size is not None and (
            not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0
        ):
            raise ValueError("expected_size must be a non-negative integer")
        if self._treeish is not None:
            if not _is_safe_relative_path(relative_path):
                return None
            size = expected_size
            if size is None:
                entry = self._tree_entry_for_path(relative_path)
                if entry is None:
                    return None
                size = entry.size
            if size is None or size > max_bytes:
                return None
            try:
                content = self._run_git_bytes(
                    ["-C", self._require_temp_dir(), "cat-file", "blob", f"{self._treeish}:{relative_path}"]
                ).stdout
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                raise GitBlobReadError("Не удалось получить содержимое файла из Git.") from error
            # Git должен вернуть ровно размер, объявленный деревом. Повторная
            # проверка защищает бюджет при изменении удалённого объекта.
            if len(content) > max_bytes or len(content) != size:
                raise GitBlobReadError("Git вернул blob с некорректным размером.")
            return content.decode("utf-8", errors="replace")
        path = self._resolve_file(relative_path)
        if path is None or not path.is_file() or path.is_symlink():
            return None
        try:
            with path.open("rb") as file:
                return file.read(max_bytes).decode("utf-8", errors="replace")
        except OSError as error:
            raise GitBlobReadError("Не удалось прочитать файл из рабочей области.") from error

    def iter_files(self, *, excluded_directories: frozenset[str]) -> Iterator[str]:
        """Возвращает обычные файлы, не покидая workspace."""

        for entry in self.iter_file_entries(excluded_directories=excluded_directories):
            yield entry.relative_path

    def iter_file_entries(
        self,
        *,
        excluded_directories: frozenset[str],
        max_entries: int = MAX_TREE_ENTRIES,
        max_tree_bytes: int = MAX_TREE_BYTES,
    ) -> Iterator[GitTreeEntry]:
        """Потоково перечисляет regular files в фиксированном бюджете дерева.

        В blobless-клоне здесь нет ``communicate`` и нет словаря всего дерева:
        запись обрабатывается сразу после ``NUL`` из ``git ls-tree -z``.
        """

        _validate_tree_limit(max_entries, "max_entries")
        _validate_tree_limit(max_tree_bytes, "max_tree_bytes")
        if self._treeish is not None:
            for entry in self._iter_blobless_tree_entries(
                max_entries=max_entries,
                max_tree_bytes=max_tree_bytes,
            ):
                if any(part in excluded_directories for part in PurePosixPath(entry.relative_path).parts[:-1]):
                    continue
                yield entry
            return
        if self.temp_dir is None:
            return
        root = Path(self.temp_dir).resolve()
        seen_entries = 0
        seen_tree_bytes = 0
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
                    relative_path = str(candidate.resolve().relative_to(root))
                except ValueError:
                    continue
                seen_entries += 1
                seen_tree_bytes += len(relative_path.encode("utf-8", errors="surrogateescape"))
                if seen_entries > max_entries or seen_tree_bytes > max_tree_bytes:
                    raise GitTreeLimitError("Дерево репозитория превысило безопасный лимит анализа.")
                try:
                    size = candidate.stat().st_size
                except OSError as error:
                    raise GitBlobReadError("Не удалось прочитать метаданные файла.") from error
                yield GitTreeEntry(relative_path, size)

    def file_size(self, relative_path: str) -> int | None:
        if self._treeish is not None:
            entry = self._tree_entry_for_path(relative_path)
            return None if entry is None else entry.size
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
            self._git_command(arguments),
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
            self._git_command(arguments),
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
            environment["GIT_CONFIG_KEY_0"] = f"http.https://{self._clone_host}/.extraHeader"
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

    def _iter_blobless_tree_entries(
        self,
        *,
        max_entries: int,
        max_tree_bytes: int,
    ) -> Iterator[GitTreeEntry]:
        """Потоково парсит ``ls-tree`` в рамках бюджета метаданных.

        Здесь принципиально нет ``subprocess.run(..., stdout=PIPE)``: тот
        сначала собрал бы всё дерево в памяти и только потом дал бы нам шанс
        заметить превышение ``max_entries``. Генератор останавливает Git при
        первом байте сверх бюджета, а готовая запись сразу отдаётся вызывающему
        коду и не сохраняется в словаре.
        """

        pending = bytearray()
        seen_entries = 0
        seen_tree_bytes = 0
        for chunk in self._iter_git_tree_chunks(max_tree_bytes=max_tree_bytes):
            seen_tree_bytes += len(chunk)
            if seen_tree_bytes > max_tree_bytes:
                raise GitTreeLimitError("Метаданные дерева Git превысили безопасный лимит.")
            pending.extend(chunk)
            while True:
                separator = pending.find(b"\0")
                if separator < 0:
                    if len(pending) > _MAX_TREE_RECORD_BYTES:
                        raise GitTreeLimitError("Запись дерева Git превысила безопасный лимит.")
                    break
                record = bytes(pending[:separator])
                del pending[: separator + 1]
                if not record:
                    continue
                seen_entries += 1
                if seen_entries > max_entries:
                    raise GitTreeLimitError("Дерево репозитория превысило безопасный лимит анализа.")
                entry = _parse_tree_record(record)
                if entry is not None:
                    yield entry
        if pending:
            raise GitCloneError("Git вернул незавершённую запись дерева репозитория.")

    def _iter_git_tree_chunks(self, *, max_tree_bytes: int) -> Iterator[bytes]:
        """Читает вывод ``ls-tree`` небольшими блоками с лимитом и timeout."""

        command = self._git_command(
            ["-C", self._require_temp_dir(), "ls-tree", "-r", "-z", "-l", self._require_treeish()]
        )
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self._git_environment(),
            )
        except OSError as error:
            raise GitCloneError("Не удалось запустить безопасное чтение дерева репозитория.") from error
        assert process.stdout is not None
        emitted_bytes = 0
        deadline = time.monotonic() + self._timeout_seconds
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, self._timeout_seconds)
                readable, _, _ = select.select([process.stdout], [], [], remaining)
                if not readable:
                    raise subprocess.TimeoutExpired(command, self._timeout_seconds)
                # Считываем не более одного байта сверх лимита, поэтому даже
                # злонамеренно большое дерево не образует крупный буфер в RAM.
                allowed = max_tree_bytes - emitted_bytes + 1
                chunk = os.read(process.stdout.fileno(), min(_TREE_STREAM_CHUNK_BYTES, allowed))
                if not chunk:
                    break
                emitted_bytes += len(chunk)
                if emitted_bytes > max_tree_bytes:
                    raise GitTreeLimitError("Метаданные дерева Git превысили безопасный лимит.")
                yield chunk
            if process.wait(timeout=max(0.0, deadline - time.monotonic())) != 0:
                raise subprocess.CalledProcessError(process.returncode, command)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise GitCloneError("Не удалось безопасно получить дерево репозитория.") from error
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()

    def _tree_entry_for_path(self, relative_path: str) -> GitTreeEntry | None:
        """Возвращает одну запись дерева, не перечисляя весь репозиторий."""

        if not _is_safe_relative_path(relative_path):
            return None
        try:
            completed = self._run_git_bytes(
                [
                    "-C",
                    self._require_temp_dir(),
                    "ls-tree",
                    "-z",
                    "-l",
                    self._require_treeish(),
                    "--",
                    relative_path,
                ]
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise GitBlobReadError("Не удалось получить метаданные файла из Git.") from error
        if len(completed.stdout) > _MAX_TREE_RECORD_BYTES:
            raise GitBlobReadError("Git вернул слишком большую запись файла.")
        records = [record for record in completed.stdout.split(b"\0") if record]
        if not records:
            return None
        if len(records) != 1:
            raise GitBlobReadError("Git вернул неоднозначную запись файла.")
        entry = _parse_tree_record(records[0])
        if entry is None or entry.relative_path != relative_path:
            return None
        return entry

    def _require_temp_dir(self) -> str:
        if self.temp_dir is None:
            raise GitCloneError("Git-репозиторий ещё не подготовлен.")
        return self.temp_dir

    def _require_treeish(self) -> str:
        if self._treeish is None:
            raise GitCloneError("Git-дерево репозитория ещё не подготовлено.")
        return self._treeish

    @staticmethod
    def _git_command(arguments: list[str]) -> list[str]:
        return [
            "git",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "http.followRedirects=false",
            *arguments,
        ]

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
        or parsed.hostname not in SOURCECRAFT_GIT_HOSTS
        or parsed.port not in (None, 443)
        or parsed.username not in (None, "git")
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not valid_path
    ):
        raise ValueError("repository URL must use the official SourceCraft Git HTTPS host")


def _validate_tree_limit(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be positive")


def _is_safe_relative_path(relative_path: object) -> bool:
    if not isinstance(relative_path, str) or not relative_path or "\0" in relative_path:
        return False
    path = PurePosixPath(relative_path)
    return not path.is_absolute() and ".." not in path.parts


def _parse_tree_record(record: bytes) -> GitTreeEntry | None:
    """Преобразует одну NUL-разделённую запись ``git ls-tree -l``."""

    metadata, separator, raw_path = record.partition(b"\t")
    fields = metadata.split()
    if separator != b"\t" or len(fields) != 4:
        raise GitCloneError("Git вернул некорректное дерево репозитория.")
    mode, object_type, _object_id, raw_size = fields
    if mode.decode("ascii", errors="ignore") not in _REGULAR_GIT_FILE_MODES or object_type != b"blob":
        return None
    relative_path = raw_path.decode("utf-8", errors="surrogateescape")
    if not _is_safe_relative_path(relative_path):
        raise GitCloneError("Git вернул небезопасный путь в дереве репозитория.")
    try:
        size = int(raw_size) if raw_size != b"-" else None
    except ValueError as error:
        raise GitCloneError("Git вернул некорректный размер файла.") from error
    if size is not None and size < 0:
        raise GitCloneError("Git вернул некорректный размер файла.")
    return GitTreeEntry(relative_path, size)


def _remove_readonly(function: object, path: str, _: object) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    if callable(function):
        function(path)
