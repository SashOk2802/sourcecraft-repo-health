"""Локальная git-рабочая область для анализа файлов репозитория.

Класс выполняет git строго через list-form вызовы без shell-интерполяции,
перехватывает stderr для диагностики и никогда не включает URL или токен
в тексты пользовательских ошибок (требование блока «не светить секреты»).
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DEFAULT_GIT_TIMEOUT_SECONDS = 60.0
_NO_SHALLOW_COMMITS = "no commits selected for shallow requests"


class GitCloneError(RuntimeError):
    """Ошибка клонирования/подготовки рабочей области.

    Текст исключения не содержит URL репозитория и токен аутентификации.
    """


class GitOperationError(GitCloneError):
    """Неуспешный exit-код git-команды внутри подготовленной рабочей области."""


def _redact(value: str, *, repo_url: str | None, token: str | None) -> str:
    """Убирает URL репозитория и токен из строки перед её показом."""
    redacted = value
    if token:
        redacted = redacted.replace(token, "<token>")
    if repo_url:
        redacted = redacted.replace(repo_url, "<repo>")
    return redacted


class LocalGitRepository:
    """Управляет временной рабочей областью для анализа файлов репозитория."""

    def __init__(
        self,
        repo_url: str,
        ref: str | None = None,
        *,
        auth_token: str | None = None,
        timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    ) -> None:
        """Принимает URL, ссылку для checkout и токен аутентификации.

        ``ref`` может быть именем ветки, тегом или полным commit SHA.
        ``auth_token`` передаётся в git как Bearer-заголовок (тот же
        механизм, что у SourceCraftClient), поэтому не попадает в URL.
        """
        self.repo_url = repo_url
        self.ref = ref
        self._auth_token = auth_token
        self._timeout_seconds = timeout_seconds
        self.temp_dir: str | None = None

    @property
    def auth_token(self) -> str | None:
        """Токен аутентификации (нужен только для локального редактирования текстов)."""
        return self._auth_token

    def redact(self, value: str) -> str:
        """Убирает URL репозитория и токен из строки для пользовательских сообщений."""
        return _redact(value, repo_url=self.repo_url, token=self._auth_token)

    def clone(self) -> str:
        """Клонирует репозиторий во временную папку и переключается на ``ref``.

        Для веток и тегов используется shallow clone этой ссылки. Если ``ref``
        оказался commit SHA (например, ``AnalysisContext.commit_sha``), а не именем
        ветки, клонируем дефолтную ветку поверхностно и подтягиваем нужный коммит
        адресно (работает при поддержке сервера, как в GitHub/GitLab).
        """
        # Повторный clone без предварительной cleanup не должен молча терять
        # предыдущую временную директорию: убираем её, если она ещё существует.
        if self.temp_dir is not None:
            self.cleanup()

        self.temp_dir = tempfile.mkdtemp(prefix="repo-health-")
        try:
            if self.ref:
                self._clone_shallow_with_ref(self.ref)
            else:
                self._run_git(["clone", "--depth", "1", self.repo_url, self.temp_dir])
            return self.temp_dir
        except subprocess.TimeoutExpired as error:
            self.cleanup()
            raise GitCloneError(
                "Клонирование репозитория превысило допустимое время ожидания."
            ) from error
        except subprocess.CalledProcessError as error:
            self.cleanup()
            raise GitCloneError(
                "Не удалось получить содержимое репозитория: git-команда завершилась ошибкой."
            ) from error

    def _clone_shallow_with_ref(self, ref: str) -> None:
        try:
            self._run_git(["clone", "--branch", ref, "--depth", "1", self.repo_url, self.temp_dir])
            return
        except subprocess.CalledProcessError:
            # ref, вероятно, commit SHA, а не ветка/тег: клонируем дефолтную ветку
            # и подтягиваем нужный коммит адресно (fetch по хешу поддерживается
            # GitHub/GitLab-подобными серверами; этот fallback покрыт тестом T.2).
            self.cleanup()
            self.temp_dir = tempfile.mkdtemp(prefix="repo-health-")
            self._run_git(["clone", "--depth", "1", self.repo_url, self.temp_dir])
            self._run_git(["-C", self.temp_dir, "fetch", "--depth", "1", "origin", ref])
            self._run_git(["-C", self.temp_dir, "checkout", "--detach", "FETCH_HEAD"])

    def _run_git(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        command = ["git"]
        if self._auth_token:
            # Тот же Bearer-PAT, что и у API-клиента; в URL токен не попадает.
            command += ["-c", f"http.extraheader=AUTHORIZATION: Bearer {self._auth_token}"]
        command += arguments

        try:
            return subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise
        except subprocess.CalledProcessError as error:
            diagnostic = _redact(
                error.stderr or "",
                repo_url=self.repo_url,
                token=self._auth_token,
            )
            logger.debug(
                "git %s failed (exit %s): %s",
                arguments[0],
                error.returncode,
                diagnostic,
            )
            raise

    def file_exists(self, relative_path: str) -> bool:
        """Проверяет наличие файла по относительному пути от корня репозитория."""
        if not self.temp_dir:
            return False
        return self._resolve_within_temp(relative_path) is not None

    def read_file(self, relative_path: str) -> str | None:
        """Безопасно читает содержимое файла (делегирует read_file_safe).

        Обратная совместимость: полный лимит размера применяется внутри
        :meth:`read_file_safe` со значением по умолчанию (1 MiB).
        """
        return self.read_file_safe(relative_path)

    def read_file_safe(self, relative_path: str, max_bytes: int = 1_048_576) -> str | None:
        """Безопасно читает содержимое файла с ограничением размера.

        Путь нормализуется через realpath и обязан находиться внутри временной
        директории; ``..``, абсолютные пути и симлинки наружу отклоняются
        (см. :meth:`_resolve_within_temp`). Читается не более ``max_bytes`` байт —
        файл больше лимита обрезается, а не читается целиком. Возвращает ``None``
        при попытке выхода за пределы temp_dir или ошибке чтения.
        """
        full_path = self._resolve_within_temp(relative_path)
        if full_path is None:
            return None
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read(max_bytes)
        except OSError:
            return None

    def _resolve_within_temp(self, relative_path: str) -> str | None:
        """Возвращает realpath внутри temp_dir либо ``None`` при попытке выхода."""
        if not self.temp_dir:
            return None
        root = os.path.realpath(self.temp_dir)
        candidate = os.path.realpath(os.path.join(self.temp_dir, relative_path))
        if candidate == root:
            return None
        if not candidate.startswith(root + os.sep):
            return None
        return candidate

    def cleanup(self) -> None:
        """Удаляет временную папку.

        Обязательно к вызову после завершения анализа. Ошибка удаления не
        пробрасывается наружу: она не должна затирать уже собранный результат
        категории (Windows не позволяет удалять read-only файлы .git).
        """
        temp_dir, self.temp_dir = self.temp_dir, None
        if not temp_dir:
            return
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, onerror=_force_remove_readonly)
        except OSError:
            logger.warning("Не удалось удалить временную директорию git-клона.", exc_info=True)


@dataclass(frozen=True, slots=True)
class CommitTimestampPage:
    """Метки времени коммитов за окно и признак, что выборка оборвана бюджетом."""

    committed_at: tuple[datetime, ...]
    truncated: bool


def read_commit_timestamps(
    repo_url: str,
    *,
    since: datetime,
    until: datetime,
    revision: str,
    auth_token: str | None = None,
    max_commits: int = 20_000,
    timeout_seconds: float = 90.0,
) -> CommitTimestampPage:
    """Читает даты коммитов, достижимых из ``revision``, без содержимого файлов.

    ``revision`` — SHA, уже зафиксированный для этого запуска. ``git log`` идёт
    от него, а не от HEAD клона: коммиты, появившиеся на ветке позже, в метрику
    не входят. Клон отдельный и поверхностный по дате, рабочая копия
    документации остаётся ``--depth 1``. ``--filter=blob:none`` и
    ``--no-checkout`` не материализуют файлы. Клон и ``git log`` делят один
    бюджет ``timeout_seconds``. При обрыве бюджета или ошибке git вызывающий
    код обязан не подменять это нулём баллов категории.
    """

    if since.tzinfo is None or until.tzinfo is None:
        raise ValueError("since and until must be timezone-aware")
    if until < since:
        raise ValueError("until must not be earlier than since")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("revision must be a commit SHA")
    if max_commits < 1:
        raise ValueError("max_commits must be at least 1")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    since_utc = since.astimezone(UTC)
    # git --since и --until включают саму секунду. Секунда сверху на --until
    # удерживает коммит ровно в period_end, если сравнение окажется строже.
    # Коммиты вне [since, until] отсекает вызывающий код.
    until_utc = until.astimezone(UTC) + timedelta(seconds=1)
    since_text = since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    until_text = until_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    clone_url = _history_clone_url(repo_url)
    temp_dir = tempfile.mkdtemp(prefix="repo-health-history-")
    deadline = time.monotonic() + timeout_seconds
    completed: subprocess.CompletedProcess[str] | None = None
    try:
        _run_history_git(
            [
                "clone",
                "--filter=blob:none",
                f"--shallow-since={since_text}",
                "--single-branch",
                "--no-tags",
                "--no-checkout",
                clone_url,
                temp_dir,
            ],
            auth_token=auth_token,
            repo_url=clone_url,
            timeout_seconds=_remaining_timeout(deadline, timeout_seconds),
        )
        if not _commit_exists(
            temp_dir,
            revision,
            repo_url=clone_url,
            timeout_seconds=_remaining_timeout(deadline, timeout_seconds),
        ):
            try:
                _run_history_git(
                    [
                        "-C",
                        temp_dir,
                        "fetch",
                        "--filter=blob:none",
                        f"--shallow-since={since_text}",
                        "origin",
                        revision,
                    ],
                    auth_token=auth_token,
                    repo_url=clone_url,
                    timeout_seconds=_remaining_timeout(deadline, timeout_seconds),
                )
            except subprocess.CalledProcessError as error:
                # SHA старше окна: у него нет коммитов новее shallow-since.
                if _NO_SHALLOW_COMMITS in (error.stderr or ""):
                    return CommitTimestampPage(committed_at=(), truncated=False)
                raise
        completed = _run_history_git(
            [
                "-C",
                temp_dir,
                "log",
                revision,
                f"--since={since_text}",
                f"--until={until_text}",
                f"--max-count={max_commits + 1}",
                "--pretty=format:%cI",
            ],
            auth_token=None,
            repo_url=clone_url,
            timeout_seconds=_remaining_timeout(deadline, timeout_seconds),
        )
    except subprocess.TimeoutExpired as error:
        raise GitCloneError(
            "Чтение истории коммитов превысило допустимое время ожидания."
        ) from error
    except subprocess.CalledProcessError as error:
        raise GitCloneError("Не удалось прочитать историю коммитов.") from error
    finally:
        _remove_history_dir(temp_dir)

    if completed is None:
        raise GitCloneError("Не удалось прочитать историю коммитов.")

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    truncated = len(lines) > max_commits
    if truncated:
        lines = lines[:max_commits]
    committed_at = tuple(
        moment
        for moment in (_parse_git_timestamp(line) for line in lines)
        if moment is not None
    )
    return CommitTimestampPage(committed_at=committed_at, truncated=truncated)


def _remaining_timeout(deadline: float, timeout_seconds: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(cmd="git", timeout=timeout_seconds)
    return remaining


def _commit_exists(
    temp_dir: str,
    revision: str,
    *,
    repo_url: str,
    timeout_seconds: float,
) -> bool:
    try:
        _run_history_git(
            ["-C", temp_dir, "cat-file", "-e", f"{revision}^{{commit}}"],
            auth_token=None,
            repo_url=repo_url,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.CalledProcessError:
        return False
    return True


def _history_clone_url(repo_url: str) -> str:
    """Локальный путь превращает в file://, иначе git игнорирует --shallow-since."""

    if urlsplit(repo_url).scheme:
        return repo_url
    return Path(repo_url).resolve().as_uri()


def _parse_git_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _run_history_git(
    arguments: list[str],
    *,
    auth_token: str | None,
    repo_url: str,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    command = ["git"]
    if auth_token:
        command += ["-c", f"http.extraheader=AUTHORIZATION: Bearer {auth_token}"]
    command += arguments
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise
    except subprocess.CalledProcessError as error:
        diagnostic = _redact(error.stderr or "", repo_url=repo_url, token=auth_token)
        logger.debug(
            "git %s failed (exit %s): %s",
            arguments[0],
            error.returncode,
            diagnostic,
        )
        raise


def _remove_history_dir(temp_dir: str) -> None:
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, onerror=_force_remove_readonly)
    except OSError:
        logger.warning("Не удалось удалить временную директорию истории коммитов.", exc_info=True)


def _force_remove_readonly(func: object, path: str, exc_info: object) -> None:
    """Снимает read-only атрибут (например, у файлов .git на Windows) и повторяет."""
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    if callable(func):
        func(path)
