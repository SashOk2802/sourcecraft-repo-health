"""Безопасный bridge между локальным SourceCraft CLI и web-анализатором.

Публичная REST-схема SourceCraft пока не описывает endpoint чтения AppSec
findings, а CLI не подтверждает постраничный обход. Поэтому web-worker не
запускает CLI с пользовательской IAM-сессией. Вместо этого пользовательский
процесс создаёт короткоживущий обезличенный snapshot, а worker читает его из
отдельного каталога, смонтированного только для чтения.

Snapshot содержит только уже разрешённые агрегаты ``severity/status/count``.
В нём нет токена, slug репозитория, пути файла, текста правила, фрагмента кода
или значения секрета. Он привязан к repository id через SHA-256 и к commit SHA.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from backend.app.analyzers.security import SecurityFacts, build_facts
from backend.app.contracts import AnalysisContext
from backend.app.integrations.sourcecraft_appsec_facts import build_security_facts_from_results
from backend.app.integrations.sourcecraft_appsec_probe import (
    APPSEC_ENGINES,
    AppSecFindingGroup,
    AppSecProbeResult,
)

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_DIRECTORY_ENV = "SOURCECRAFT_APPSEC_SNAPSHOT_DIR"
SNAPSHOT_MAX_AGE_ENV = "SOURCECRAFT_APPSEC_SNAPSHOT_MAX_AGE_SECONDS"
DEFAULT_SNAPSHOT_MAX_AGE_SECONDS = 3600
MAX_SNAPSHOT_BYTES = 64 * 1024
MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
_SHA256_HEX_LENGTH = 64
_SOURCECRAFT_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class AppSecProbe(Protocol):
    """Минимальный read-only контракт локального SourceCraft CLI-зонда."""

    def probe_all(self, repository: str) -> tuple[AppSecProbeResult, ...]:
        """Возвращает только обезличенные результаты SAST, SCA и secrets."""


class SourceCraftAppSecSnapshotError(RuntimeError):
    """Snapshot нельзя безопасно использовать для анализа."""


class SourceCraftAppSecSnapshotExportError(RuntimeError):
    """Локальный exporter не смог доказать безопасную привязку snapshot'а."""


@dataclass(frozen=True, slots=True)
class SourceCraftAppSecSnapshotSettings:
    """Настройки read-only каталога с безопасными AppSec snapshot'ами."""

    directory: Path
    max_age: timedelta

    def __post_init__(self) -> None:
        if not self.directory.is_absolute():
            raise ValueError("SourceCraft AppSec snapshot directory must be absolute")
        if self.max_age <= timedelta(0):
            raise ValueError("SourceCraft AppSec snapshot max age must be positive")


def snapshot_settings_from_environment(
    environ: Mapping[str, str] | None = None,
) -> SourceCraftAppSecSnapshotSettings | None:
    """Читает опциональную конфигурацию bridge без открытия файлов.

    Отсутствующая переменная означает, что AppSec данных для worker нет — это
    штатный ``unavailable``, а не ошибка настройки. Относительный путь не
    принимается: при Docker-монте он мог бы незаметно указать не тот каталог.
    """

    values = os.environ if environ is None else environ
    raw_directory = values.get(SNAPSHOT_DIRECTORY_ENV, "").strip()
    if not raw_directory:
        return None
    if "\x00" in raw_directory:
        raise ValueError("SourceCraft AppSec snapshot directory contains a NUL byte")

    raw_age = values.get(SNAPSHOT_MAX_AGE_ENV, str(DEFAULT_SNAPSHOT_MAX_AGE_SECONDS)).strip()
    try:
        max_age_seconds = int(raw_age)
    except ValueError as error:
        raise ValueError("SourceCraft AppSec snapshot max age must be an integer") from error
    if max_age_seconds <= 0:
        raise ValueError("SourceCraft AppSec snapshot max age must be positive")
    return SourceCraftAppSecSnapshotSettings(
        directory=Path(raw_directory),
        max_age=timedelta(seconds=max_age_seconds),
    )


def repository_fingerprint(repository_id: str) -> str:
    """Связывает snapshot с внутренним id репозитория без записи id в файл."""

    if not isinstance(repository_id, str) or not repository_id:
        raise ValueError("SourceCraft repository id must be a non-empty string")
    return hashlib.sha256(repository_id.encode("utf-8")).hexdigest()


def snapshot_filename(repository_id: str) -> str:
    """Возвращает безопасное детерминированное имя snapshot-файла."""

    return f"{repository_fingerprint(repository_id)}.json"


def write_snapshot(
    directory: Path,
    *,
    repository_id: str,
    collected_at: datetime,
    results: tuple[AppSecProbeResult, ...],
    commit_sha: str | None = None,
) -> Path:
    """Атомарно пишет безопасный snapshot с правами владельца ``0600``.

    Функция используется только локальным exporter'ом, который уже получил
    сводку через пользовательскую авторизацию CLI. Она не принимает и не пишет
    исходные findings.
    """

    if not directory.is_absolute():
        raise ValueError("SourceCraft AppSec snapshot directory must be absolute")
    payload = _snapshot_payload(repository_id, collected_at, results, commit_sha=commit_sha)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ValueError("SourceCraft AppSec snapshot exceeds the safe size limit")

    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_metadata = os.lstat(directory)
    if stat.S_ISLNK(directory_metadata.st_mode) or not stat.S_ISDIR(directory_metadata.st_mode):
        raise ValueError("SourceCraft AppSec snapshot directory must be a real directory")
    if directory_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("SourceCraft AppSec snapshot directory must not be group or world writable")
    destination = directory / snapshot_filename(repository_id)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".appsec-", dir=directory)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, destination)
        os.chmod(destination, 0o600)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return destination


class SourceCraftAppSecCliSnapshotExporter:
    """Создаёт commit-bound snapshot через локально авторизованный SourceCraft CLI.

    ``repository_id`` не передаётся оператором вручную: exporter читает его из
    официального API по тому же OWNER/REPOSITORY. Commit тоже не принимается
    аргументом: его обязаны вернуть все доступные AppSec scan'ы в ``latestCommit``.
    Это не даёт подменить результаты одного репозитория или scan'а данными другого.
    """

    def __init__(
        self,
        probe: AppSecProbe,
        *,
        cli_binary: str = "src",
        timeout_seconds: int = 20,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not isinstance(cli_binary, str) or not cli_binary.strip():
            raise ValueError("cli_binary must be a non-empty string")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
            raise TypeError("timeout_seconds must be an integer")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._probe = probe
        self._cli_binary = cli_binary
        self._timeout_seconds = timeout_seconds
        self._runner = runner
        self._clock = clock

    def export(self, repository: str, directory: Path) -> Path:
        """Собирает safe агрегаты и записывает snapshot без raw AppSec-данных."""

        owner, name = _parse_repository(repository)
        try:
            results = tuple(self._probe.probe_all(repository))
        except Exception as error:
            raise SourceCraftAppSecSnapshotExportError("SourceCraft AppSec probe failed") from error
        if not _has_exact_engine_set(results):
            raise SourceCraftAppSecSnapshotExportError("SourceCraft AppSec probe returned an invalid engine set")

        # Сначала убеждаемся, что результаты действительно относятся к одному
        # commit. До этого момента не нужен даже запрос metadata репозитория:
        # так exporter не создаёт файл и не делает лишний сетевой вызов для
        # неподтверждённого scan'а.
        try:
            _confirmed_scan_commit(results)
        except ValueError as error:
            raise SourceCraftAppSecSnapshotExportError(
                "SourceCraft AppSec scan cannot be bound to a commit"
            ) from error
        repository_id = self._repository_id(owner, name)
        try:
            return write_snapshot(
                directory,
                repository_id=repository_id,
                collected_at=_as_utc(self._clock()),
                results=results,
            )
        except (OSError, TypeError, ValueError) as error:
            raise SourceCraftAppSecSnapshotExportError("SourceCraft AppSec snapshot cannot be bound safely") from error

    def _repository_id(self, owner: str, name: str) -> str:
        command = [
            self._cli_binary,
            "api",
            "-X",
            "GET",
            f"repos/{owner}/{name}",
            "--json",
        ]
        try:
            completed = self._runner(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=self._timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SourceCraftAppSecSnapshotExportError("SourceCraft repository metadata is unavailable") from error
        if completed.returncode != 0:
            # stderr может включать slug, детали авторизации или сообщение API.
            raise SourceCraftAppSecSnapshotExportError("SourceCraft repository metadata is unavailable")
        try:
            payload = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise SourceCraftAppSecSnapshotExportError("SourceCraft repository metadata is invalid") from error
        repository_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(repository_id, str) or not repository_id.strip() or "\x00" in repository_id:
            raise SourceCraftAppSecSnapshotExportError("SourceCraft repository metadata is invalid")
        return repository_id


class SourceCraftAppSecSnapshotStore:
    """Читает свежий snapshot, подходящий ровно одному запуску анализа."""

    def __init__(
        self,
        settings: SourceCraftAppSecSnapshotSettings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._clock = clock

    def collect(self, context: AnalysisContext) -> SecurityFacts:
        """Возвращает безопасные факты либо честную недоступность источника."""

        try:
            path = _snapshot_path(self._settings.directory, context.repository.id)
        except SourceCraftAppSecSnapshotError:
            return build_facts(None, source_error="sourcecraft_appsec_snapshot_unreadable")
        if path is None:
            return build_facts(None)
        try:
            raw_snapshot = _read_snapshot(path)
        except SourceCraftAppSecSnapshotError:
            return build_facts(None, source_error="sourcecraft_appsec_snapshot_unreadable")
        if raw_snapshot is None:
            return build_facts(None)

        try:
            results = _parse_snapshot(
                raw_snapshot,
                repository_id=context.repository.id,
                commit_sha=context.commit_sha,
                now=_as_utc(self._clock()),
                max_age=self._settings.max_age,
            )
        except _StaleOrMismatchedSnapshot:
            return build_facts(None)
        except SourceCraftAppSecSnapshotError:
            return build_facts(None, source_error="sourcecraft_appsec_snapshot_invalid")
        return build_security_facts_from_results(results)


class _StaleOrMismatchedSnapshot(SourceCraftAppSecSnapshotError):
    """Ожидаемое отсутствие актуальных данных, не ошибка источника."""


def _snapshot_path(directory: Path, repository_id: str) -> Path | None:
    """Разрешает файл только внутри существующего real directory, не symlink'а."""

    try:
        metadata = os.lstat(directory)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SourceCraftAppSecSnapshotError("snapshot directory cannot be inspected") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SourceCraftAppSecSnapshotError("snapshot directory must be a real directory")
    return directory / snapshot_filename(repository_id)


def _snapshot_payload(
    repository_id: str,
    collected_at: datetime,
    results: tuple[AppSecProbeResult, ...],
    *,
    commit_sha: str | None,
) -> dict[str, object]:
    if not isinstance(collected_at, datetime) or collected_at.tzinfo is None:
        raise ValueError("SourceCraft AppSec snapshot collection time must include a timezone")
    if not _has_exact_engine_set(results):
        raise ValueError("SourceCraft AppSec snapshot must contain every engine exactly once")
    sourcecraft_commit_sha = _confirmed_scan_commit(results)
    if commit_sha is not None:
        _validate_commit_sha(commit_sha)
        if not hmac.compare_digest(commit_sha, sourcecraft_commit_sha):
            raise ValueError("explicit AppSec snapshot commit must match the SourceCraft scan commit")
    ordered_results = tuple(
        next(result for result in results if result.engine == engine) for engine in APPSEC_ENGINES
    )
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "repository_id_sha256": repository_fingerprint(repository_id),
        "commit_sha": sourcecraft_commit_sha,
        "collected_at": _as_utc(collected_at).isoformat(),
        "engines": [result.as_dict() for result in ordered_results],
    }


def _read_snapshot(path: Path) -> dict[str, Any] | None:
    """Читает один маленький regular file, не следуя symlink'ам."""

    no_follow_flag = getattr(os, "O_NOFOLLOW", None)
    if no_follow_flag is None:
        raise SourceCraftAppSecSnapshotError("platform cannot open snapshots without following links")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= no_follow_flag
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SourceCraftAppSecSnapshotError("snapshot cannot be opened safely") from error

    with os.fdopen(descriptor, "rb") as input_file:
        metadata = os.fstat(input_file.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise SourceCraftAppSecSnapshotError("snapshot must be a regular file")
        if metadata.st_size > MAX_SNAPSHOT_BYTES:
            raise SourceCraftAppSecSnapshotError("snapshot exceeds the safe size limit")
        encoded = input_file.read(MAX_SNAPSHOT_BYTES + 1)
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise SourceCraftAppSecSnapshotError("snapshot exceeds the safe size limit")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceCraftAppSecSnapshotError("snapshot is not valid JSON") from error
    if not isinstance(payload, dict):
        raise SourceCraftAppSecSnapshotError("snapshot must be a JSON object")
    return payload


def _parse_snapshot(
    payload: dict[str, Any],
    *,
    repository_id: str,
    commit_sha: str,
    now: datetime,
    max_age: timedelta,
) -> tuple[AppSecProbeResult, ...]:
    expected_fields = {
        "schema_version",
        "repository_id_sha256",
        "commit_sha",
        "collected_at",
        "engines",
    }
    schema_version = payload.get("schema_version")
    if (
        set(payload) != expected_fields
        or not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != SNAPSHOT_SCHEMA_VERSION
    ):
        raise SourceCraftAppSecSnapshotError("snapshot schema is not supported")

    fingerprint = payload.get("repository_id_sha256")
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != _SHA256_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in fingerprint)
    ):
        raise SourceCraftAppSecSnapshotError("snapshot repository fingerprint is invalid")
    if not hmac.compare_digest(fingerprint, repository_fingerprint(repository_id)):
        raise _StaleOrMismatchedSnapshot("snapshot belongs to another repository")

    snapshot_commit = payload.get("commit_sha")
    if not isinstance(snapshot_commit, str):
        raise SourceCraftAppSecSnapshotError("snapshot commit is invalid")
    try:
        _validate_commit_sha(snapshot_commit)
    except ValueError as error:
        raise SourceCraftAppSecSnapshotError("snapshot commit is invalid") from error
    if not hmac.compare_digest(snapshot_commit, commit_sha):
        raise _StaleOrMismatchedSnapshot("snapshot belongs to another commit")

    collected_at = _parse_timestamp(payload.get("collected_at"))
    if collected_at > now + MAX_FUTURE_CLOCK_SKEW or now - collected_at > max_age:
        raise _StaleOrMismatchedSnapshot("snapshot is not fresh")

    raw_engines = payload.get("engines")
    if not isinstance(raw_engines, list):
        raise SourceCraftAppSecSnapshotError("snapshot engines are invalid")
    results = tuple(_parse_engine(raw_engine) for raw_engine in raw_engines)
    if not _has_exact_engine_set(results):
        raise SourceCraftAppSecSnapshotError("snapshot must contain every engine exactly once")
    return results


def _parse_engine(payload: object) -> AppSecProbeResult:
    expected_fields = {
        "engine",
        "availability",
        "finding_count",
        "severities",
        "reason",
        "finding_groups",
        "completeness",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise SourceCraftAppSecSnapshotError("snapshot engine schema is invalid")

    severities = payload.get("severities")
    if not isinstance(severities, list):
        raise SourceCraftAppSecSnapshotError("snapshot engine severities are invalid")
    finding_groups = _parse_finding_groups(payload.get("finding_groups"))
    try:
        return AppSecProbeResult(
            engine=payload.get("engine"),
            availability=payload.get("availability"),
            finding_count=payload.get("finding_count"),
            severities=tuple(severities),
            reason=payload.get("reason"),
            finding_groups=finding_groups,
            completeness=payload.get("completeness"),
        )
    except (TypeError, ValueError) as error:
        raise SourceCraftAppSecSnapshotError("snapshot contains unsafe AppSec data") from error


def _parse_finding_groups(payload: object) -> tuple[AppSecFindingGroup, ...] | None:
    if payload is None:
        return None
    if not isinstance(payload, list):
        raise SourceCraftAppSecSnapshotError("snapshot finding groups are invalid")
    groups: list[AppSecFindingGroup] = []
    for group in payload:
        if not isinstance(group, dict) or set(group) != {"severity", "status", "count"}:
            raise SourceCraftAppSecSnapshotError("snapshot finding group schema is invalid")
        try:
            groups.append(
                AppSecFindingGroup(
                    severity=group.get("severity"),
                    status=group.get("status"),
                    count=group.get("count"),
                )
            )
        except (TypeError, ValueError) as error:
            raise SourceCraftAppSecSnapshotError("snapshot finding group is unsafe") from error
    return tuple(groups)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise SourceCraftAppSecSnapshotError("snapshot timestamp is invalid")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise SourceCraftAppSecSnapshotError("snapshot timestamp is invalid") from error
    if timestamp.tzinfo is None:
        raise SourceCraftAppSecSnapshotError("snapshot timestamp must include a timezone")
    return _as_utc(timestamp)


def _validate_commit_sha(commit_sha: str) -> None:
    if not isinstance(commit_sha, str) or len(commit_sha) != 40:
        raise ValueError("SourceCraft AppSec snapshot commit SHA must be 40 hexadecimal characters")
    try:
        int(commit_sha, 16)
    except ValueError as error:
        raise ValueError("SourceCraft AppSec snapshot commit SHA must be hexadecimal") from error


def _has_exact_engine_set(results: tuple[object, ...]) -> bool:
    return (
        len(results) == len(APPSEC_ENGINES)
        and all(isinstance(result, AppSecProbeResult) for result in results)
        and {result.engine for result in results if isinstance(result, AppSecProbeResult)}
        == set(APPSEC_ENGINES)
    )


def _confirmed_scan_commit(results: tuple[AppSecProbeResult, ...]) -> str:
    """Требует единый commit от SourceCraft для всех доступных scan'ов."""

    available_results = tuple(result for result in results if result.availability == "available")
    if not available_results or any(result.scan_commit_sha is None for result in available_results):
        raise ValueError("SourceCraft AppSec snapshot requires a commit from every available scan")
    commits = {result.scan_commit_sha for result in available_results}
    if len(commits) != 1:
        raise ValueError("SourceCraft available AppSec scans must report the same commit")
    commit_sha = next(iter(commits))
    assert commit_sha is not None
    return commit_sha


def _parse_repository(repository: str) -> tuple[str, str]:
    if not isinstance(repository, str):
        raise TypeError("repository must be a string in OWNER/REPOSITORY form")
    owner, separator, name = repository.partition("/")
    if (
        separator != "/"
        or not _SOURCECRAFT_SLUG.fullmatch(owner)
        or not _SOURCECRAFT_SLUG.fullmatch(name)
        or "/" in name
    ):
        raise ValueError("repository must use safe OWNER/REPOSITORY SourceCraft slugs")
    return owner, name


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)
