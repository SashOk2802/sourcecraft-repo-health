"""Bounded, opt-in AppSec API collection through the user's local CLI session.

The endpoint is observed in SourceCraft CLI 0.0.105, but is not part of the
published REST schema. Unknown shapes fail closed. Empty engine results remain
unavailable until the source can prove that that engine actually ran.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

from backend.app.integrations.sourcecraft_appsec_probe import (
    APPSEC_ENGINES,
    APPSEC_SEVERITIES,
    APPSEC_STATUSES,
    AppSecProbeResult,
    CommandRunner,
    _collect_finding_groups,
    _validate_repository,
)

APPSEC_API_URL = "https://appsec.sourcecraft.tech"
PAGE_SIZE = 100
MAX_ITEMS = 10_000
MAX_PAGES = 100
COMMAND_ATTEMPTS = 3
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
# Only numeric values independently matched against CLI's named JSON output
# on 2026-09-28. Unobserved values MUST remain unknown, never guessed.
_SEVERITIES = {1: "LOW", 2: "MEDIUM", 3: "HIGH"}
_STATUSES = {0: "OPEN"}


class _InvalidSource(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _Scan:
    uuid: str
    commit: str
    repository: str
    finished_at: int
    total: int


class SourceCraftAppSecApiProbe:
    """Collect all pages; return only safe aggregates for the snapshot bridge."""

    def __init__(
        self,
        *,
        appsec_environment: str,
        cli_binary: str = "src",
        timeout_seconds: int = 20,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        if not isinstance(appsec_environment, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,63}", appsec_environment
        ):
            raise ValueError("A safe AppSec CLI environment name is required")
        if not isinstance(cli_binary, str) or not cli_binary.strip():
            raise ValueError("cli_binary must be a non-empty string")
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")
        self._environment = appsec_environment
        self._binary = cli_binary
        self._timeout = timeout_seconds
        self._runner = runner

    def probe_all(self, repository: str) -> tuple[AppSecProbeResult, ...]:
        _validate_repository(repository)
        try:
            self._check_environment()
            metadata = self._json(["api", "-X", "GET", f"repos/{repository}"])
            repository_id = _uuid(metadata.get("id")) if isinstance(metadata, dict) else None
            if repository_id is None:
                raise _InvalidSource
            scan = self._latest_scan(repository_id)
            if scan is None:
                return _results("unavailable", "sourcecraft_appsec_unavailable")
            results = tuple(self._engine(repository_id, scan, engine) for engine in APPSEC_ENGINES)
            if sum(result.finding_count or 0 for result in results) != scan.total:
                raise _InvalidSource
            # scanUuid is not a sufficient guard: an unknown UUID was observed
            # to fall back to the latest scan. Reject rollover during collection.
            if self._latest_scan(repository_id) != scan:
                raise _InvalidSource
            return results
        except subprocess.TimeoutExpired:
            return _results("error", "sourcecraft_cli_timeout")
        except OSError:
            return _results("error", "sourcecraft_cli_unavailable")
        except (ValueError, TypeError):
            return _results("error", "sourcecraft_cli_invalid_payload")

    def _check_environment(self) -> None:
        environments = self._json(["envs", "list", "--json"])
        if not isinstance(environments, list):
            raise _InvalidSource
        matches = [
            x for x in environments if isinstance(x, dict) and x.get("name") == self._environment
        ]
        if len(matches) != 1 or matches[0].get("api") != APPSEC_API_URL:
            raise _InvalidSource
        active = [x for x in environments if isinstance(x, dict) and x.get("active") is True]
        if len(active) != 1 or active[0].get("api") != "https://api.sourcecraft.tech":
            raise _InvalidSource

    def _json(self, arguments: list[str]) -> object:
        completed = None
        for attempt in range(COMMAND_ATTEMPTS):
            try:
                completed = self._runner(
                    [self._binary, *arguments],
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=self._timeout,
                )
            except subprocess.TimeoutExpired:
                if attempt + 1 == COMMAND_ATTEMPTS:
                    raise
                continue
            if completed.returncode == 0:
                break
        if completed is None or completed.returncode != 0:
            raise _InvalidSource
        # stdout/stderr and exception text must never escape this collector.
        if len(completed.stdout) > 8 * 1024 * 1024:
            raise _InvalidSource
        return json.loads(completed.stdout)

    def _api(self, path: str, fields: dict[str, str]) -> object:
        arguments = ["--env", self._environment, "api", "-X", "GET", path]
        for key, value in fields.items():
            arguments.extend(["-f", f"{key}={value}"])
        return self._json(arguments)

    def _pages(self, path: str, fields: dict[str, str]) -> Iterator[dict]:
        token = ""
        tokens: set[str] = set()
        identities: set[str] = set()
        total: int | None = None
        count = 0
        for _ in range(MAX_PAGES):
            query = {**fields, "pageSize": str(PAGE_SIZE)}
            if token:
                query["pageToken"] = token
            page = self._api(path, query)
            if not isinstance(page, dict):
                raise _InvalidSource
            items, next_token, size = (
                page.get("data"),
                page.get("nextPageToken"),
                page.get("totalSize"),
            )
            if (
                not isinstance(items, list)
                or len(items) > PAGE_SIZE
                or not isinstance(next_token, str)
                or len(next_token) > 4096
                or type(size) is not int
                or not 0 <= size <= MAX_ITEMS
            ):
                raise _InvalidSource
            if total is not None and total != size:
                raise _InvalidSource
            total = size
            for item in items:
                if not isinstance(item, dict):
                    raise _InvalidSource
                identity = _uuid(item.get("uuid"))
                if identity in identities:
                    raise _InvalidSource
                identities.add(identity)
                count += 1
                if count > total:
                    raise _InvalidSource
                yield item
            if not next_token:
                if count != total:
                    raise _InvalidSource
                return
            if not items or count >= total or next_token in tokens:
                raise _InvalidSource
            tokens.add(next_token)
            token = next_token
        raise _InvalidSource

    def _latest_scan(self, repository_id: str) -> _Scan | None:
        latest = [
            x
            for x in self._pages("v1/scans", {"gitRepo": repository_id})
            if x.get("isLatest") is True
        ]
        if not latest:
            return None
        if len(latest) != 1 or latest[0].get("scanType") != "SCAN_TYPE_DEFAULT":
            raise _InvalidSource
        scan_id = _uuid(latest[0]["uuid"])
        detail = self._api(f"v1/scans/{scan_id}", {"gitRepo": repository_id})
        if not isinstance(detail, dict) or detail.get("uuid") != scan_id:
            raise _InvalidSource
        if detail.get("status") != "FINISHED":
            return None
        commit, source_repo = detail.get("commitHash"), detail.get("gitRepo")
        finished, total = detail.get("timeFinished"), detail.get("totalDefectGroups")
        if (
            not isinstance(commit, str)
            or not _COMMIT.fullmatch(commit)
            or commit != latest[0].get("commitHash")
            or detail.get("isLatest") is not True
            or not isinstance(source_repo, str)
            or not source_repo
            or len(source_repo) > 128
            or type(finished) is not int
            or finished <= 0
            or type(total) is not int
            or not 0 <= total <= MAX_ITEMS
            or total != latest[0].get("totalDefectGroups")
        ):
            raise _InvalidSource
        return _Scan(scan_id, commit, source_repo, finished, total)

    def _engine(self, repository_id: str, scan: _Scan, engine: str) -> AppSecProbeResult:
        safe_findings = []
        for item in self._pages(
            "v1/defect-groups",
            {
                "gitRepo": repository_id,
                "scanUuid": scan.uuid,
                "type": engine,
            },
        ):
            if item.get("latestCommit") != scan.commit or item.get("gitRepo") != scan.repository:
                raise _InvalidSource
            raw_engine = item.get("engineType")
            if raw_engine != engine and not (
                engine == "SAST" and type(raw_engine) is int and raw_engine == 3
            ):
                raise _InvalidSource
            safe_findings.append(
                {
                    "severity": _enum(item.get("severity"), _SEVERITIES, APPSEC_SEVERITIES),
                    "status": _enum(item.get("status"), _STATUSES, APPSEC_STATUSES),
                }
            )
        if not safe_findings:
            # Global FINISHED does not prove execution of each individual
            # engine. In particular, never turn disabled/absent SCA into 100.
            return AppSecProbeResult(
                engine, "unavailable", None, reason="sourcecraft_appsec_unavailable"
            )
        groups = _collect_finding_groups(safe_findings)
        return AppSecProbeResult(
            engine,
            "available",
            len(safe_findings),
            severities=tuple(sorted({g.severity for g in groups if g.severity is not None})),
            finding_groups=groups,
            completeness="complete",
            scan_commit_sha=scan.commit,
            scan_uuid=scan.uuid,
        )


def _uuid(value: object) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
        raise _InvalidSource
    return value


def _enum(value: object, mapping: dict[int, str], allowed: frozenset[str]) -> str | None:
    if isinstance(value, str):
        return value if value in allowed else None
    return mapping.get(value) if type(value) is int else None


def _results(availability: str, reason: str) -> tuple[AppSecProbeResult, ...]:
    return tuple(AppSecProbeResult(e, availability, None, reason=reason) for e in APPSEC_ENGINES)
