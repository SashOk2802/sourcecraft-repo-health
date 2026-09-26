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
AppSecCompleteness = Literal["unknown", "complete"]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
_KNOWN_SEVERITIES = frozenset({"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"})
# Это только уже наблюдавшиеся в SourceCraft безопасные статусы. Неизвестная
# строка никогда не попадает в сводку: она превращается в ``None`` в группе.
_KNOWN_STATUSES = frozenset({"OPEN", "TRIAGED_TP", "RESOLVED_FP", "RESOLVED_TOLERABLE"})
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
class AppSecFindingGroup:
    """Обезличенная группа finding'ов с одинаковыми severity и status.

    ``None`` означает, что поле отсутствовало либо его значение пока не
    подтверждено контрактом SourceCraft. Исходное значение не сохраняется.
    """

    severity: str | None
    status: str | None
    count: int

    def __post_init__(self) -> None:
        if self.severity is not None and self.severity not in _KNOWN_SEVERITIES:
            raise ValueError("AppSec finding group contains an unknown severity")
        if self.status is not None and self.status not in _KNOWN_STATUSES:
            raise ValueError("AppSec finding group contains an unknown status")
        if not isinstance(self.count, int) or isinstance(self.count, bool):
            raise TypeError("AppSec finding group count must be an integer")
        if self.count <= 0:
            raise ValueError("AppSec finding group count must be positive")

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "severity": self.severity,
            "status": self.status,
            "count": self.count,
        }


@dataclass(frozen=True, slots=True)
class AppSecProbeResult:
    """Безопасный итог одного обращения к движку AppSec.

    ``finding_count`` показывает число элементов в ограниченной CLI-выборке,
    поэтому это не число всех уязвимостей и не оценка безопасности.
    ``finding_groups`` содержит только агрегированные разрешённые значения;
    ``completeness`` явно не позволяет спутать ограниченную выборку с полным
    результатом сканирования.
    """

    engine: str
    availability: AppSecAvailability
    finding_count: int | None
    severities: tuple[str, ...] = ()
    reason: str | None = None
    finding_groups: tuple[AppSecFindingGroup, ...] | None = None
    completeness: AppSecCompleteness | None = None

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
        if self.availability == "available" and self.completeness not in {"unknown", "complete"}:
            raise ValueError("available AppSec result must declare completeness")
        if self.completeness == "complete" and self.finding_groups is None:
            raise ValueError("complete AppSec result must provide safe finding groups")
        if self.availability != "available" and self.completeness is not None:
            raise ValueError("unavailable or error AppSec result cannot declare completeness")
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
        if self.finding_groups is not None:
            if not isinstance(self.finding_groups, tuple):
                raise TypeError("finding_groups must be a tuple or None")
            if not all(isinstance(group, AppSecFindingGroup) for group in self.finding_groups):
                raise TypeError("finding_groups must contain AppSecFindingGroup values")
            group_keys = {(group.severity, group.status) for group in self.finding_groups}
            if len(group_keys) != len(self.finding_groups):
                raise ValueError("AppSec finding groups must be unique")
            if self.availability != "available" and self.finding_groups:
                raise ValueError("unavailable or error AppSec result cannot have finding groups")
            if self.finding_count is not None and sum(group.count for group in self.finding_groups) != self.finding_count:
                raise ValueError("AppSec finding groups must account for every finding")
            grouped_severities = tuple(
                sorted({group.severity for group in self.finding_groups if group.severity is not None})
            )
            if grouped_severities != self.severities:
                raise ValueError("AppSec finding groups must match result severities")

    def as_dict(self) -> dict[str, str | int | list[dict[str, str | int | None]] | list[str] | None]:
        """Возвращает JSON-представление, в котором нет сырых findings."""

        return {
            "engine": self.engine,
            "availability": self.availability,
            "finding_count": self.finding_count,
            "severities": list(self.severities),
            "reason": self.reason,
            "finding_groups": (
                None if self.finding_groups is None else [group.as_dict() for group in self.finding_groups]
            ),
            "completeness": self.completeness,
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

    finding_groups = _collect_finding_groups(payload)
    return AppSecProbeResult(
        engine=engine,
        availability="available",
        finding_count=len(payload),
        severities=tuple(sorted({group.severity for group in finding_groups if group.severity})),
        finding_groups=finding_groups,
        # У CLI пока нет подтверждённого постраничного контракта. Даже ответ
        # меньше limit нельзя выдавать за полный scan без отдельной проверки.
        completeness="unknown",
    )


def _collect_finding_groups(findings: list[dict[str, Any]]) -> tuple[AppSecFindingGroup, ...]:
    """Агрегирует разрешённые severity/status, не копируя contents finding'ов."""

    counts: dict[tuple[str | None, str | None], int] = {}
    for finding in findings:
        key = (_normalize_severity(finding.get("severity")), _normalize_status(finding.get("status")))
        counts[key] = counts.get(key, 0) + 1

    return tuple(
        AppSecFindingGroup(severity=severity, status=status, count=count)
        for (severity, status), count in sorted(
            counts.items(),
            key=lambda item: (item[0][0] or "", item[0][1] or ""),
        )
    )


def _normalize_severity(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized in _KNOWN_SEVERITIES else None


def _normalize_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized in _KNOWN_STATUSES else None


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
