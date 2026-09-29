"""Локальная git-рабочая область для анализа файлов репозитория.

Класс выполняет git строго через list-form вызовы без shell-интерполяции,
перехватывает stderr для диагностики и никогда не включает URL или токен
в тексты пользовательских ошибок (требование блока «не светить секреты»).
"""

from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from backend.app._env import env_float, env_int
from backend.app.integrations.sourcecraft import SourceCraftClient

logger = logging.getLogger(__name__)


# Лимиты для безопасной работы с репозиториями, удовлетворяющие критерию
# крупного репозитория по ТЗ (>= 500 МБ рабочей копии, >= 20 000 коммитов, >= 10 000 файлов)
DEFAULT_GIT_TIMEOUT_SECONDS = env_float("SOURCECRAFT_GIT_TIMEOUT_SECONDS", 120.0)
DEFAULT_MAX_GIT_FILES = env_int("SOURCECRAFT_MAX_GIT_FILES", 50_000)
# Допускаем крупные единичные блобы (vendored-библиотеки, бинарные ассеты, prebuilt bundles)
# до 50 МБ. Суммарный объём дерева строго ограничен DEFAULT_MAX_GIT_TREE_BYTES (600 МБ),
# поэтому отдельные большие файлы не приводят к неконтролируемому раздуванию рабочей области.
DEFAULT_MAX_GIT_BLOB_BYTES = env_int("SOURCECRAFT_MAX_GIT_BLOB_BYTES", 50 * 1024 * 1024)
DEFAULT_MAX_GIT_TREE_BYTES = env_int("SOURCECRAFT_MAX_GIT_TREE_BYTES", 600 * 1024 * 1024)
DEFAULT_MAX_GIT_CHECKOUT_BYTES = env_int("SOURCECRAFT_MAX_GIT_CHECKOUT_BYTES", 750 * 1024 * 1024)
DEFAULT_GIT_WORKSPACE_POLL_SECONDS = 0.01
WINDOWS_PROCESS_TREE_KILL_TIMEOUT_SECONDS = 5.0
_WINDOWS_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_NO_SHALLOW_COMMITS = "no commits selected for shallow requests"
_FULL_COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.IGNORECASE)


def sourcecraft_git_http_authorization(token: str) -> str:
    """Возвращает Basic-заголовок Git Smart HTTP для SourceCraft PAT.

    SourceCraft принимает PAT как пароль HTTPS Git-подключения. Заголовок
    формируется только для короткоживущего окружения git-процесса, а не для URL
    или аргументов команды.
    """

    if not isinstance(token, str) or not token:
        raise ValueError("SourceCraft Git token must not be blank")
    credentials = base64.b64encode(f"git:{token}".encode()).decode("ascii")
    return f"AUTHORIZATION: Basic {credentials}"


class GitCloneError(RuntimeError):
    """Ошибка клонирования/подготовки рабочей области.

    Текст исключения не содержит URL репозитория и токен аутентификации.
    """


class GitOperationError(GitCloneError):
    """Неуспешный exit-код git-команды внутри подготовленной рабочей области."""


@dataclass(frozen=True, slots=True)
class GitCheckoutLimits:
    """Жёсткие бюджеты private-клона до materialization рабочего дерева."""

    max_files: int = field(
        default_factory=lambda: env_int("SOURCECRAFT_MAX_GIT_FILES", DEFAULT_MAX_GIT_FILES)
    )
    max_blob_bytes: int = field(
        default_factory=lambda: env_int(
            "SOURCECRAFT_MAX_GIT_BLOB_BYTES", DEFAULT_MAX_GIT_BLOB_BYTES
        )
    )
    max_tree_bytes: int = field(
        default_factory=lambda: env_int(
            "SOURCECRAFT_MAX_GIT_TREE_BYTES", DEFAULT_MAX_GIT_TREE_BYTES
        )
    )
    max_checkout_bytes: int = field(
        default_factory=lambda: env_int(
            "SOURCECRAFT_MAX_GIT_CHECKOUT_BYTES", DEFAULT_MAX_GIT_CHECKOUT_BYTES
        )
    )

    def __post_init__(self) -> None:
        if min(
            self.max_files,
            self.max_blob_bytes,
            self.max_tree_bytes,
            self.max_checkout_bytes,
        ) < 1:
            raise ValueError("git checkout limits must be positive")


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
        timeout_seconds: float | None = None,
        limits: GitCheckoutLimits | None = None,
    ) -> None:
        """Принимает URL, ссылку для checkout и токен аутентификации.

        ``ref`` может быть именем ветки, тегом или полным commit SHA.
        ``auth_token`` передаётся в Git Smart HTTP как Basic-пароль SourceCraft
        только через окружение процесса, поэтому не попадает в URL или argv.
        """
        self.repo_url = repo_url
        self.ref = ref
        self._auth_token = auth_token
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else env_float("SOURCECRAFT_GIT_TIMEOUT_SECONDS", DEFAULT_GIT_TIMEOUT_SECONDS)
        )
        self._limits = limits
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
            if self._limits is not None:
                self._clone_bounded(self.ref)
            elif self.ref:
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
        except GitCloneError:
            self.cleanup()
            raise
        finally:
            # Credential больше не нужен после clone/fetch и не остаётся в
            # workspace, который затем читают анализаторы.
            self._auth_token = None

    def _clone_bounded(self, ref: str | None) -> None:
        limits = self._limits
        assert limits is not None
        if self._auth_token and not SourceCraftClient.is_official_git_clone_url(self.repo_url):
            raise GitCloneError(
                "Для чтения приватного репозитория нужен официальный HTTPS-адрес SourceCraft."
            )
        filter_spec = f"blob:limit={limits.max_blob_bytes}"
        self._run_git(
            [
                "clone",
                "--quiet",
                "--depth",
                "1",
                "--filter",
                filter_spec,
                "--no-checkout",
                self.repo_url,
                self.temp_dir,
            ],
            workspace_limit_bytes=limits.max_checkout_bytes,
        )
        self._validate_checkout_size(limits.max_checkout_bytes)

        target = "HEAD"
        if ref:
            self._run_git(
                [
                    "-C",
                    self.temp_dir,
                    "fetch",
                    "--quiet",
                    "--depth",
                    "1",
                    "--filter",
                    filter_spec,
                    "origin",
                    ref,
                ],
                workspace_limit_bytes=limits.max_checkout_bytes,
            )
            target = "FETCH_HEAD"

        self._validate_tree(target, limits)
        self._run_git(
            ["-C", self.temp_dir, "checkout", "--quiet", "--detach", target],
            no_lazy_fetch=True,
            workspace_limit_bytes=limits.max_checkout_bytes,
        )
        self._validate_checkout_size(limits.max_checkout_bytes)

    def _validate_tree(self, target: str, limits: GitCheckoutLimits) -> None:
        """Проверяет число и размеры blob до checkout с ограниченной памятью."""

        command = [
            "git",
            "-C",
            str(self.temp_dir),
            "ls-tree",
            "-r",
            "--format=%(objecttype) %(objectsize)",
            target,
        ]
        env = self._git_environment(no_lazy_fetch=True)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )
        file_count = 0
        total_bytes = 0
        deadline = time.monotonic() + self._timeout_seconds
        watchdog = threading.Timer(self._timeout_seconds, process.kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, self._timeout_seconds)
                object_type, separator, raw_size = line.strip().partition(" ")
                if object_type != "blob" or not separator or not raw_size.isdigit():
                    raise GitCloneError("Git-дерево содержит неподдерживаемый объект.")
                file_count += 1
                blob_bytes = int(raw_size)
                total_bytes += blob_bytes
                if file_count > limits.max_files:
                    raise GitCloneError("Git-дерево превышает лимит числа файлов.")
                if blob_bytes > limits.max_blob_bytes:
                    raise GitCloneError("Git-дерево содержит слишком большой файл.")
                if total_bytes > limits.max_tree_bytes:
                    raise GitCloneError("Git-дерево превышает лимит общего размера.")
            remaining = max(0.001, deadline - time.monotonic())
            return_code = process.wait(timeout=remaining)
            if return_code != 0:
                raise GitCloneError("Не удалось проверить размер Git-дерева.")
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()

    def _validate_checkout_size(self, max_bytes: int) -> None:
        total = 0
        if self.temp_dir is None:
            raise GitCloneError("Git workspace не создан.")
        for root, _, files in os.walk(self.temp_dir):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except FileNotFoundError:
                    # Git переименовывает временные pack/index-файлы во время
                    # записи. Исчезнувший между os.walk и getsize путь уже не
                    # занимает место и не должен давать ложную ошибку.
                    continue
                except OSError as error:
                    raise GitCloneError("Не удалось проверить размер Git workspace.") from error
                if total > max_bytes:
                    raise GitCloneError("Git workspace превышает лимит размера.")

    @staticmethod
    def _kill_process_tree(process: subprocess.Popen[str]) -> None:
        """Останавливает git и дочерние transport/index-pack процессы."""

        try:
            process_id = process.pid
        except AttributeError:
            process.kill()
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process_id), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=WINDOWS_PROCESS_TREE_KILL_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired):
                # taskkill входит в Windows, но отказ его запуска не должен
                # оставить хотя бы родительский git.exe работающим.
                pass
            if process.poll() is None:
                process.kill()
            return
        else:
            try:
                os.killpg(os.getpgid(process_id), signal.SIGKILL)
                return
            except ProcessLookupError:
                return
            except OSError:
                pass
        process.kill()

    def _run_git_with_workspace_limit(
        self,
        command: list[str],
        *,
        env: dict[str, str],
        max_bytes: int,
    ) -> subprocess.CompletedProcess[str]:
        """Запускает git и ограничивает workspace во время скачивания/checkout.

        Проверка после ``clone`` недостаточна: один допустимый blob может быть мал,
        но pack из тысяч blob способен занять весь диск. Поэтому процесс запускается
        отдельно, а временный каталог измеряется, пока transport/index-pack ещё
        работают. При превышении бюджета завершается вся группа процессов.
        """

        process_options: dict[str, object] = {}
        if os.name == "nt":
            # Отдельная группа делает дерево Git явной единицей управления;
            # taskkill /T ниже завершает git.exe вместе с transport/index-pack.
            process_options["creationflags"] = _WINDOWS_NEW_PROCESS_GROUP
        else:
            process_options["start_new_session"] = True
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
            **process_options,
        )
        deadline = time.monotonic() + self._timeout_seconds
        try:
            while process.poll() is None:
                self._validate_checkout_size(max_bytes)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, self._timeout_seconds)
                time.sleep(min(DEFAULT_GIT_WORKSPACE_POLL_SECONDS, remaining))

            self._validate_checkout_size(max_bytes)
            return_code = process.wait()
            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, command)
            return subprocess.CompletedProcess(command, return_code, "", "")
        except BaseException:
            if process.poll() is None:
                self._kill_process_tree(process)
                process.wait()
            raise

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

    def _run_git(
        self,
        arguments: list[str],
        *,
        no_lazy_fetch: bool = False,
        workspace_limit_bytes: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git"]
        command += arguments
        operation = arguments[0] if arguments else ""
        if operation == "-C" and len(arguments) > 2:
            operation = arguments[2]
        use_auth = self._auth_token is not None and operation in {"clone", "fetch"}
        env = self._git_environment(use_auth=use_auth, no_lazy_fetch=no_lazy_fetch)

        try:
            if workspace_limit_bytes is not None:
                return self._run_git_with_workspace_limit(
                    command,
                    env=env,
                    max_bytes=workspace_limit_bytes,
                )
            output_options = (
                {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
                if self._auth_token
                else {"capture_output": True}
            )
            return subprocess.run(
                command,
                check=True,
                text=True,
                timeout=self._timeout_seconds,
                env=env,
                **output_options,
            )
        except subprocess.TimeoutExpired:
            raise
        except subprocess.CalledProcessError as error:
            if self._auth_token:
                logger.debug(
                    "authenticated git %s failed (exit %s)",
                    operation,
                    error.returncode,
                )
            else:
                diagnostic = _redact(
                    error.stderr or "",
                    repo_url=self.repo_url,
                    token=None,
                )
                logger.debug(
                    "git %s failed (exit %s): %s",
                    operation,
                    error.returncode,
                    diagnostic,
                )
            raise

    def _git_environment(
        self,
        *,
        use_auth: bool = False,
        no_lazy_fetch: bool = False,
    ) -> dict[str, str]:
        env = os.environ.copy()
        for key in tuple(env):
            if key == "GIT_CONFIG_COUNT" or key.startswith(
                ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
            ):
                env.pop(key, None)
        env.pop("GIT_NO_LAZY_FETCH", None)
        env["GIT_TERMINAL_PROMPT"] = "0"
        if use_auth and self._auth_token:
            # Credential передаётся только окружению короткой clone/fetch
            # операции и не виден в URL или argv процесса.
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "http.extraheader"
            env["GIT_CONFIG_VALUE_0"] = sourcecraft_git_http_authorization(
                self._auth_token
            )
        if no_lazy_fetch:
            env["GIT_NO_LAZY_FETCH"] = "1"
        return env

    def file_exists(self, relative_path: str) -> bool:
        """Проверяет наличие обычного файла в рабочей области клона.

        Каталог, отсутствующий путь и симлинк за пределы временной директории не
        считаются файлом. Иначе каталог с именем README.md ошибочно дал бы
        положительную оценку документации.
        """
        if not self.temp_dir:
            return False
        full_path = self._resolve_within_temp(relative_path)
        return full_path is not None and os.path.isfile(full_path)

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


def _force_remove_readonly(func: object, path: str, exc_info: object) -> None:
    """Снимает read-only атрибут (например, у файлов .git на Windows) и повторяет."""
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    if callable(func):
        func(path)


@dataclass(frozen=True, slots=True)
class CommitRecord:
    """Один коммит из истории: время, автор и число родителей.

    Email автора нужен только для подсчёта bus factor внутри анализа и в отчёт
    не попадает. ``parent_count`` >= 2 означает merge-коммит.
    """

    committed_at: datetime
    author_email: str
    parent_count: int


@dataclass(frozen=True, slots=True)
class CommitTimestampPage:
    """Коммиты за окно и признак, что выборка оборвана бюджетом.

    ``committed_at`` — все даты в окне, включая merge. Ими пользуется Activity
    для активных недель. ``commits`` — те же записи с автором и родителями
    для bus factor; merge в формулу bus factor не входят.
    """

    committed_at: tuple[datetime, ...]
    truncated: bool
    commits: tuple[CommitRecord, ...] = ()


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

    Формат строки лога: ``%cI%x00%aE%x00%P`` — дата, email автора и родители.
    Активные недели Activity берут все даты; bus factor отбрасывает merge.
    """

    if since.tzinfo is None or until.tzinfo is None:
        raise ValueError("since and until must be timezone-aware")
    if until < since:
        raise ValueError("until must not be earlier than since")
    if not isinstance(revision, str) or _FULL_COMMIT_SHA.fullmatch(revision) is None:
        raise ValueError("revision must be a full commit SHA")
    if max_commits < 1:
        raise ValueError("max_commits must be at least 1")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    since_utc = since.astimezone(UTC)
    period_until_utc = until.astimezone(UTC)
    # Граница передаётся git без расширения: посторонние коммиты не должны
    # занимать место в ограниченной выборке. Результат дополнительно
    # фильтруется по тому же точному интервалу.
    since_text = since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    until_text = period_until_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    clone_url = _history_clone_url(repo_url)
    if auth_token and not SourceCraftClient.is_official_git_clone_url(clone_url):
        raise GitCloneError(
            "Для чтения приватной истории нужен официальный HTTPS-адрес SourceCraft."
        )
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
                "--pretty=format:%cI%x00%aE%x00%P",
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
    commits = tuple(
        record
        for record in (_parse_commit_log_line(line) for line in lines)
        if record is not None and since_utc <= record.committed_at <= period_until_utc
    )
    return CommitTimestampPage(
        committed_at=tuple(record.committed_at for record in commits),
        truncated=truncated,
        commits=commits,
    )


def _parse_commit_log_line(line: str) -> CommitRecord | None:
    """Разбирает одну строку ``%cI\\0%aE\\0%P``. Битая строка пропускается."""

    parts = line.split("\0")
    if len(parts) < 2:
        # Старый формат только с датой: активные недели сохраняются, bus factor
        # без автора не считается.
        moment = _parse_git_timestamp(line)
        if moment is None:
            return None
        return CommitRecord(committed_at=moment, author_email="", parent_count=1)

    moment = _parse_git_timestamp(parts[0])
    if moment is None:
        return None
    author_email = parts[1].strip().lower()
    parents = parts[2].split() if len(parts) > 2 and parts[2].strip() else []
    return CommitRecord(
        committed_at=moment,
        author_email=author_email,
        parent_count=len(parents) if parents else 1,
    )


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
    command = ["git", *arguments]
    env = os.environ.copy()
    for key in tuple(env):
        if key == "GIT_CONFIG_COUNT" or key.startswith(
            ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
        ):
            env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if auth_token:
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = sourcecraft_git_http_authorization(auth_token)

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
