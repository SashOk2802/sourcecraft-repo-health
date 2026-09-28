"""Хранение неизменяемых снимков завершённых анализов."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from threading import RLock
from typing import TYPE_CHECKING, Protocol

import asyncpg

from backend.app.analysis.runner import AnalysisExecution
from backend.app.reporting import build_report_payload, render_markdown_report

if TYPE_CHECKING:
    from backend.app.analysis.jobs import AnalysisJob, AnalysisJobStatus


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    """Готовое неизменяемое представление анализа для выдачи через API."""

    report: dict[str, object]
    markdown: str


@dataclass(frozen=True, slots=True)
class StoredAnalysisSnapshot:
    """Снимок вместе с идентификатором, необходимым для перехода к отчёту."""

    analysis_id: str
    snapshot: AnalysisSnapshot


class AnalysisStore(Protocol):
    """Получение и сохранение снимков анализов по их стабильному идентификатору."""

    async def start(self) -> None:
        """Подготавливает внешние ресурсы хранилища."""

    async def close(self) -> None:
        """Освобождает внешние ресурсы хранилища."""

    async def get(self, analysis_id: str) -> AnalysisSnapshot | None:
        """Возвращает готовый снимок анализа или None, если его нет."""

    async def save(self, analysis_id: str, execution: AnalysisExecution) -> None:
        """Сохраняет новый неизменяемый снимок завершённого анализа."""

    async def list_latest_for_repositories(
        self,
        repository_ids: Collection[str],
    ) -> tuple[StoredAnalysisSnapshot, ...]:
        """Возвращает последний снимок каждой пары «репозиторий + методика»."""

    async def get_latest_for_repository_slug(
        self,
        organization_slug: str,
        repository_slug: str,
    ) -> StoredAnalysisSnapshot | None:
        """Возвращает последний снимок репозитория по его org и repo слагам."""


class InMemoryAnalysisStore:
    """Временное хранилище для разработки и HTTP-тестов без PostgreSQL."""

    def __init__(self) -> None:
        self._snapshots: dict[str, AnalysisSnapshot] = {}
        self._lock = RLock()

    async def start(self) -> None:
        """Не требует отдельной подготовки."""

    async def close(self) -> None:
        """Не удерживает внешние ресурсы."""

    async def get(self, analysis_id: str) -> AnalysisSnapshot | None:
        with self._lock:
            snapshot = self._snapshots.get(normalize_analysis_id(analysis_id))
            return _copy_snapshot(snapshot) if snapshot is not None else None

    async def save(self, analysis_id: str, execution: AnalysisExecution) -> None:
        normalized_id = normalize_analysis_id(analysis_id)
        snapshot = _build_snapshot(execution, normalized_id)

        with self._lock:
            if normalized_id in self._snapshots:
                raise ValueError("analysis_id already exists")
            self._snapshots[normalized_id] = snapshot

    async def list_latest_for_repositories(
        self,
        repository_ids: Collection[str],
    ) -> tuple[StoredAnalysisSnapshot, ...]:
        identifiers = _normalize_repository_ids(repository_ids)
        if not identifiers:
            return ()

        with self._lock:
            snapshots = tuple(
                StoredAnalysisSnapshot(analysis_id, _copy_snapshot(snapshot))
                for analysis_id, snapshot in self._snapshots.items()
                if _snapshot_repository_id(snapshot) in identifiers
            )
        return _latest_snapshots(snapshots)

    async def get_latest_for_repository_slug(
        self,
        organization_slug: str,
        repository_slug: str,
    ) -> StoredAnalysisSnapshot | None:
        org_normalized = organization_slug.strip().lower()
        repo_normalized = repository_slug.strip().lower()
        if not org_normalized or not repo_normalized:
            return None

        with self._lock:
            matches = [
                StoredAnalysisSnapshot(analysis_id, _copy_snapshot(snapshot))
                for analysis_id, snapshot in self._snapshots.items()
                if _snapshot_org_slug(snapshot).lower() == org_normalized
                and _snapshot_repo_slug(snapshot).lower() == repo_normalized
            ]
        if not matches:
            return None
        return _latest_snapshots(matches)[0]


class PostgresAnalysisStore:
    """PostgreSQL-хранилище неизменяемых снимков анализов."""

    def __init__(self, database_url: str) -> None:
        self._database_url = _normalize_database_url(database_url)
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        """Открывает пул подключений после применения миграций Alembic."""

        if self._pool is not None:
            return

        self._pool = await asyncpg.create_pool(self._database_url)

    async def close(self) -> None:
        """Закрывает пул подключений PostgreSQL."""

        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def get(self, analysis_id: str) -> AnalysisSnapshot | None:
        """Читает готовый JSON- и Markdown-отчёт без повторного расчёта Score."""

        normalized_id = normalize_analysis_id(analysis_id)
        row = await self._require_pool().fetchrow(
            """
            SELECT report, markdown
            FROM analysis_snapshots
            WHERE analysis_id = $1
            """,
            normalized_id,
        )
        if row is None:
            return None

        return AnalysisSnapshot(
            report=_json_object(row["report"]),
            markdown=str(row["markdown"]),
        )

    async def save(self, analysis_id: str, execution: AnalysisExecution) -> None:
        """Сохраняет исходный результат runner и готовые представления одного запуска."""

        normalized_id = normalize_analysis_id(analysis_id)
        snapshot = _build_snapshot(execution, normalized_id)
        payload = json.dumps(_to_json_value(execution), ensure_ascii=False, separators=(",", ":"))
        report = json.dumps(snapshot.report, ensure_ascii=False, separators=(",", ":"))

        try:
            await self._require_pool().execute(
                """
                INSERT INTO analysis_snapshots (analysis_id, payload, report, markdown)
                VALUES ($1, $2::jsonb, $3::jsonb, $4)
                """,
                normalized_id,
                payload,
                report,
                snapshot.markdown,
            )
        except asyncpg.UniqueViolationError as error:
            raise ValueError("analysis_id already exists") from error

    async def list_latest_for_repositories(
        self,
        repository_ids: Collection[str],
    ) -> tuple[StoredAnalysisSnapshot, ...]:
        identifiers = _normalize_repository_ids(repository_ids)
        if not identifiers:
            return ()

        rows = await self._require_pool().fetch(
            """
            SELECT analysis_id, report, markdown
            FROM analysis_snapshots
            WHERE report #>> '{repository,id}' = ANY($1::text[])
            """,
            list(identifiers),
        )
        snapshots = tuple(
            StoredAnalysisSnapshot(
                analysis_id=str(row["analysis_id"]),
                snapshot=AnalysisSnapshot(
                    report=_json_object(row["report"]),
                    markdown=str(row["markdown"]),
                ),
            )
            for row in rows
        )
        return _latest_snapshots(snapshots)

    async def get_latest_for_repository_slug(
        self,
        organization_slug: str,
        repository_slug: str,
    ) -> StoredAnalysisSnapshot | None:
        org_normalized = organization_slug.strip().lower()
        repo_normalized = repository_slug.strip().lower()
        if not org_normalized or not repo_normalized:
            return None

        row = await self._require_pool().fetchrow(
            """
            SELECT analysis_id, report, markdown
            FROM analysis_snapshots
            WHERE lower(report #>> '{repository,organizationSlug}') = $1
              AND lower(report #>> '{repository,repositorySlug}') = $2
            ORDER BY (report #>> '{analysis,analyzedAt}')::timestamptz DESC
            LIMIT 1
            """,
            org_normalized,
            repo_normalized,
        )
        if row is None:
            return None
        return StoredAnalysisSnapshot(
            analysis_id=str(row["analysis_id"]),
            snapshot=AnalysisSnapshot(
                report=_json_object(row["report"]),
                markdown=str(row["markdown"]),
            ),
        )

    @property
    def database_url(self) -> str:
        """Возвращает нормализованный URL базы для проверки конфигурации."""

        return self._database_url

    async def save_and_finish(
        self,
        analysis_id: str,
        execution: AnalysisExecution,
        *,
        status: AnalysisJobStatus,
        finished_at: datetime,
        worker_id: str | None = None,
    ) -> AnalysisJob:
        """Сохраняет снимок и terminal-статус задания одной PostgreSQL-транзакцией."""

        from backend.app.analysis.jobs import (
            AnalysisJobNotFoundError,
            AnalysisJobStatus,
            AnalysisJobTransitionError,
            _job_from_row,
        )

        normalized_id = normalize_analysis_id(analysis_id)
        snapshot = _build_snapshot(execution, normalized_id)
        payload = json.dumps(_to_json_value(execution), ensure_ascii=False, separators=(",", ":"))
        report = json.dumps(snapshot.report, ensure_ascii=False, separators=(",", ":"))

        try:
            async with (
                self._require_pool().acquire() as connection,
                connection.transaction(),
            ):
                current_row = await connection.fetchrow(
                    """
                    SELECT
                        analysis_id, repository_id, owner_subject, status, created_at,
                        started_at, finished_at, error_code, error_summary, worker_id
                    FROM analysis_jobs
                    WHERE analysis_id = $1
                    FOR UPDATE
                    """,
                    normalized_id,
                )
                if current_row is None:
                    raise AnalysisJobNotFoundError("analysis job not found")

                current = _job_from_row(current_row)
                if current.worker_id != worker_id:
                    raise AnalysisJobTransitionError("job belongs to another worker")
                updated = current.finished(
                    status=status,
                    finished_at=finished_at,
                )
                await connection.execute(
                    """
                    INSERT INTO analysis_snapshots (analysis_id, payload, report, markdown)
                    VALUES ($1, $2::jsonb, $3::jsonb, $4)
                    """,
                    normalized_id,
                    payload,
                    report,
                    snapshot.markdown,
                )
                updated_row = await connection.fetchrow(
                    """
                    UPDATE analysis_jobs
                    SET status = $2, finished_at = $3, error_code = $4, error_summary = $5
                    WHERE analysis_id = $1 AND status = $6
                        AND worker_id IS NOT DISTINCT FROM $7
                    RETURNING
                        analysis_id, repository_id, owner_subject, status, created_at,
                        started_at, finished_at, error_code, error_summary, worker_id
                    """,
                    updated.analysis_id,
                    updated.status.value,
                    updated.finished_at,
                    updated.error_code,
                    updated.error_summary,
                    AnalysisJobStatus.RUNNING.value,
                    worker_id,
                )
                if updated_row is None:
                    raise AnalysisJobTransitionError(
                        "job state changed before it could finish"
                    )
        except asyncpg.UniqueViolationError as error:
            raise ValueError("analysis_id already exists") from error

        return _job_from_row(updated_row)

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Analysis store is not started.")
        return self._pool


def _normalize_repository_ids(repository_ids: Collection[str]) -> frozenset[str]:
    if not isinstance(repository_ids, Collection):
        raise TypeError("repository_ids must be a collection of strings")
    if len(repository_ids) > 10_000:
        raise ValueError("repository_ids must contain at most 10000 values")

    normalized: set[str] = set()
    for repository_id in repository_ids:
        if not isinstance(repository_id, str) or not repository_id.strip():
            raise ValueError("repository_ids must contain nonblank strings")
        normalized.add(repository_id.strip())
    return frozenset(normalized)


def _latest_snapshots(
    snapshots: Collection[StoredAnalysisSnapshot],
) -> tuple[StoredAnalysisSnapshot, ...]:
    latest: dict[tuple[str, str], StoredAnalysisSnapshot] = {}
    for stored_snapshot in snapshots:
        key = (
            _snapshot_repository_id(stored_snapshot.snapshot),
            _snapshot_methodology_version(stored_snapshot.snapshot),
        )
        previous = latest.get(key)
        if previous is None or _snapshot_order_key(stored_snapshot) > _snapshot_order_key(previous):
            latest[key] = stored_snapshot
    return tuple(latest[key] for key in sorted(latest))


def _snapshot_repository_id(snapshot: AnalysisSnapshot) -> str:
    return _snapshot_string(snapshot, "repository", "id")


def _snapshot_org_slug(snapshot: AnalysisSnapshot) -> str:
    return _snapshot_string(snapshot, "repository", "organizationSlug")


def _snapshot_repo_slug(snapshot: AnalysisSnapshot) -> str:
    return _snapshot_string(snapshot, "repository", "repositorySlug")


def _snapshot_methodology_version(snapshot: AnalysisSnapshot) -> str:
    return _snapshot_string(snapshot, "analysis", "methodologyVersion")


def _snapshot_order_key(stored_snapshot: StoredAnalysisSnapshot) -> tuple[datetime, str]:
    timestamp = _snapshot_string(stored_snapshot.snapshot, "analysis", "analyzedAt")
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise ValueError("stored snapshot analyzedAt must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored snapshot analyzedAt must include a timezone")
    return parsed.astimezone(UTC), stored_snapshot.analysis_id


def _snapshot_string(snapshot: AnalysisSnapshot, section: str, field: str) -> str:
    container = snapshot.report.get(section)
    if not isinstance(container, dict):
        raise TypeError("stored snapshot report section must be an object")
    value = container.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("stored snapshot report is invalid")
    return value.strip()


def _build_snapshot(execution: AnalysisExecution, analysis_id: str) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        report=build_report_payload(execution, analysis_id=analysis_id),
        markdown=render_markdown_report(execution, analysis_id=analysis_id),
    )


def _copy_snapshot(snapshot: AnalysisSnapshot) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        report=_json_object(json.dumps(snapshot.report, ensure_ascii=False)),
        markdown=snapshot.markdown,
    )


def normalize_analysis_id(analysis_id: str) -> str:
    """Нормализует ID, безопасный для одного сегмента URL и ключа хранилища."""

    normalized_id = analysis_id.strip()
    first_character = normalized_id[:1]
    contains_only_url_safe_characters = all(
        character.isascii() and (character.isalnum() or character in "._~-")
        for character in normalized_id
    )
    if (
        not 1 <= len(normalized_id) <= 128
        or not first_character.isascii()
        or not first_character.isalnum()
        or not contains_only_url_safe_characters
    ):
        raise ValueError(
            "analysis_id must contain 1-128 URL-safe characters and start with a letter or digit"
        )
    return normalized_id


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+asyncpg://")
    return database_url


def _json_object(value: object) -> dict[str, object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise TypeError("stored report must be a JSON object")
    return json.loads(json.dumps(decoded, ensure_ascii=False))


def _to_json_value(value: object) -> object:
    if is_dataclass(value):
        return _to_json_value(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_to_json_value(item) for item in value]
    return value
