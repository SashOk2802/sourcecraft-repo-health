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

logger = logging.getLogger(__name__)

DEFAULT_GIT_TIMEOUT_SECONDS = 60.0


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
            self._run_git(
                ["clone", "--branch", ref, "--depth", "1", self.repo_url, self.temp_dir]
            )
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

    def read_file_safe(
        self, relative_path: str, max_bytes: int = 1_048_576
    ) -> str | None:
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


def _force_remove_readonly(func: object, path: str, exc_info: object) -> None:
    """Снимает read-only атрибут (например, у файлов .git на Windows) и повторяет."""
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    if callable(func):
        func(path)
