"""Безопасная сводка AppSec из локально авторизованного SourceCraft CLI.

Это инструмент разработки для проверки доступности данных, а не поставщик для
web-сервиса. CLI использует локальную IAM-сессию пользователя; токены в команды
не передаются. Сырые finding'и остаются внутри процесса и не возвращаются
вызывающему коду, чтобы в отчёт или лог не попали фрагменты кода либо секреты.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

_SOURCECRAFT_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
APPSEC_ENGINES = ("SAST", "SCA", "SECRETS")
APPSEC_SAMPLE_LIMIT = 100
AppSecAvailability = Literal["available", "unavailable", "error"]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
_KNOWN_SEVERITIES = frozenset({"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"})
_SAFE_REASONS = frozenset(
    {
        "sourcecraft_appsec_unavailable",
        "sourcecraft_cli_timeout",
        "sourcecraft_cli_unavailable",
        "sourcecraft_cli_error",
        "sourcecraft_cli_invalid_json",
        "sourcecraft_cli_invalid_payload",
    }
)


@dataclass(frozen=True, slots=True)
class AppSecProbeResult:
    """Безопасный итог одного обращения к движку AppSec.

    ``finding_count`` показывает число элементов в ограниченной CLI-выборке,
    поэтому это не число всех уязвимостей и не оценка безопасности.
    """

    engine: str
    availability: AppSecAvailability
    finding_count: int | None
    severities: tuple[str, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.engine not in APPSEC_ENGINES:
            raise ValueError("Unknown SourceCraft AppSec engine")
        if self.availability not in {"available", "unavailable", "error"}:
            raise ValueError("Unknown SourceCraft AppSec availability")
        if self.finding_count is not None and (
            not isinstance(self.finding_count, int) or isinstance(self.finding_count, bool)
        ):
            raise TypeError("finding_count must be an integer or None")
        if self.finding_count is not None and not 0 <= self.finding_count <= APPSEC_SAMPLE_LIMIT:
            raise ValueError("finding_count must fit the configured AppSec sample limit")
        if not isinstance(self.severities, tuple):
            raise TypeError("severities must be a tuple")
        if not all(isinstance(severity, str) for severity in self.severities):
            raise TypeError("severities must contain strings")
        if len(set(self.severities)) != len(self.severities):
            raise ValueError("AppSec result severities must be unique")
        if self.availability == "available" and self.finding_count is None:
            raise ValueError("available AppSec result must have finding_count")
        if self.availability != "available" and self.finding_count is not None:
            raise ValueError("unavailable or error AppSec result cannot have finding_count")
        if self.availability != "available" and self.severities:
            raise ValueError("unavailable or error AppSec result cannot have severities")
        if self.availability == "available" and self.reason is not None:
            raise ValueError("available AppSec result cannot have a reason")
        if self.availability != "available" and self.reason not in _SAFE_REASONS:
            raise ValueError("AppSec result must use a known safe reason")
        if any(severity not in _KNOWN_SEVERITIES for severity in self.severities):
            raise ValueError("AppSec result contains an unknown severity")
        if self.finding_count is not None and len(self.severities) > self.finding_count:
            raise ValueError("AppSec result cannot have more severities than findings")

    def as_dict(self) -> dict[str, str | int | list[str] | None]:
        """Возвращает JSON-представление, в котором нет сырых findings."""

        return {
            "engine": self.engine,
            "availability": self.availability,
            "finding_count": self.finding_count,
            "severities": list(self.severities),
            "reason": self.reason,
        }


class SourceCraftAppSecCliProbe:
    """Вызывает ``src appsec defect list`` и оставляет только безопасную сводку."""

    def __init__(
        self,
        *,
        cli_binary: str = "src",
        timeout_seconds: int = 20,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        if not isinstance(cli_binary, str) or not cli_binary.strip():
            raise ValueError("cli_binary must be a non-empty string")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
            raise TypeError("timeout_seconds must be an integer")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._cli_binary = cli_binary
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def probe_all(self, repository: str) -> tuple[AppSecProbeResult, ...]:
        """Проверяет SAST, SCA и secret scanning независимо друг от друга."""

        return tuple(self.probe(repository, engine) for engine in APPSEC_ENGINES)

    def probe(self, repository: str, engine: str) -> AppSecProbeResult:
        """Проверяет один движок без передачи токена и без запуска сканирования."""

        _validate_repository(repository)
        if engine not in APPSEC_ENGINES:
            raise ValueError("Unknown SourceCraft AppSec engine")

        command = [
            self._cli_binary,
            "appsec",
            "defect",
            "list",
            "--repo",
            repository,
            "--type",
            engine,
            "--limit",
            str(APPSEC_SAMPLE_LIMIT),
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
        except subprocess.TimeoutExpired:
            return _error_result(engine, "sourcecraft_cli_timeout")
        except OSError:
            return _error_result(engine, "sourcecraft_cli_unavailable")

        if completed.returncode != 0:
            # Не добавляем stderr: CLI может включить туда имя репозитория или детали finding'а.
            return _error_result(engine, "sourcecraft_cli_error")

        return _parse_result(engine, completed.stdout)


def _parse_result(engine: str, stdout: str) -> AppSecProbeResult:
    try:
        payload = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        return _error_result(engine, "sourcecraft_cli_invalid_json")

    if payload is None:
        # Наблюдённый контракт CLI для репозитория без доступного результата сканирования.
        return AppSecProbeResult(
            engine=engine,
            availability="unavailable",
            finding_count=None,
            reason="sourcecraft_appsec_unavailable",
        )

    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        return _error_result(engine, "sourcecraft_cli_invalid_payload")

    severities = _collect_severities(payload)
    return AppSecProbeResult(
        engine=engine,
        availability="available",
        finding_count=len(payload),
        severities=severities,
    )


def _collect_severities(findings: list[dict[str, Any]]) -> tuple[str, ...]:
    """Извлекает только агрегируемую критичность, не копируя contents finding'ов."""

    values = {
        normalized
        for finding in findings
        if isinstance(severity := finding.get("severity"), str) and severity.strip()
        if (normalized := severity.strip().upper()) in _KNOWN_SEVERITIES
    }
    return tuple(sorted(values))


def _error_result(engine: str, reason: str) -> AppSecProbeResult:
    return AppSecProbeResult(
        engine=engine,
        availability="error",
        finding_count=None,
        reason=reason,
    )


def _validate_repository(repository: str) -> None:
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
