"""Состояния запусков анализа и их постоянное хранение."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol

import asyncpg

from backend.app.analysis.store import normalize_analysis_id


class AnalysisJobStatus(StrEnum):
    """Состояние одного запуска анализа репозитория."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class AnalysisJobNotFoundError(LookupError):
    """Запуск с указанным идентификатором отсутствует."""


class AnalysisJobTransitionError(RuntimeError):
    """Переход между состояниями запуска запрещён."""


_TERMINAL_STATUSES = frozenset(
    {
        AnalysisJobStatus.COMPLETED,
        AnalysisJobStatus.PARTIAL,
        AnalysisJobStatus.FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class AnalysisJob:
    """Неизменяемый снимок состояния одного запуска анализа."""

    analysis_id: str
    repository_id: str
    status: AnalysisJobStatus
    created_at: datetime
    owner_subject: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = None
    worker_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AnalysisJobStatus):
            raise TypeError("status must be an AnalysisJobStatus")
        if self.analysis_id != normalize_analysis_id(self.analysis_id):
            raise ValueError("analysis_id must not contain surrounding whitespace")
        if not self.repository_id.strip():
            raise ValueError("repository_id must not be empty")
        if self.owner_subject is not None:
            _require_owner_subject(self.owner_subject)
        if self.worker_id is not None and not self.worker_id.strip():
            raise ValueError("worker_id must not be empty")
        _require_timezone(self.created_at, "created_at")
        _validate_job_state(self)

    @classmethod
    def queued(
        cls,
        *,
        analysis_id: str,
        repository_id: str,
        created_at: datetime,
        owner_subject: str,
        worker_id: str | None = None,
    ) -> AnalysisJob:
        """Создаёт задание, ожидающее обработчик.

        owner_subject — subject сессии инициатора. Сырой Bearer сюда не кладётся.
        """

        _require_owner_subject(owner_subject)
        return cls(
            analysis_id=normalize_analysis_id(analysis_id),
            repository_id=repository_id.strip(),
            status=AnalysisJobStatus.QUEUED,
            created_at=created_at,
            owner_subject=owner_subject,
            worker_id=worker_id.strip() if worker_id is not None else None,
        )

    def started(self, started_at: datetime) -> AnalysisJob:
        """Переводит ожидающее задание в состояние выполнения."""

        if self.status is not AnalysisJobStatus.QUEUED:
            raise AnalysisJobTransitionError("only queued jobs can start")
        _require_timezone(started_at, "started_at")
        if started_at < self.created_at:
            raise AnalysisJobTransitionError("started_at must not be before created_at")
        return replace(self, status=AnalysisJobStatus.RUNNING, started_at=started_at)

    def finished(
        self,
        *,
        status: AnalysisJobStatus,
        finished_at: datetime,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> AnalysisJob:
        """Завершает выполняемое задание итоговым состоянием."""

        if self.status is not AnalysisJobStatus.RUNNING:
            raise AnalysisJobTransitionError("only running jobs can finish")
        _require_timezone(finished_at, "finished_at")
        if status not in _TERMINAL_STATUSES:
            raise AnalysisJobTransitionError("a job must finish in a terminal status")
        if self.started_at is not None and finished_at < self.started_at:
            raise AnalysisJobTransitionError("finished_at must not be before started_at")

        return replace(
            self,
            status=status,
            finished_at=finished_at,
            error_code=error_code,
            error_summary=error_summary,
        )

    def interrupted(self, finished_at: datetime) -> AnalysisJob:
        """Помечает queued или running задание ошибкой после перезапуска worker."""

        if self.status not in {AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING}:
            raise AnalysisJobTransitionError("only incomplete jobs can be recovered")
        _require_timezone(finished_at, "finished_at")
        started_at = self.started_at or self.created_at
        if finished_at < started_at:
            raise AnalysisJobTransitionError("finished_at must not be before started_at")
        return replace(
            self,
            status=AnalysisJobStatus.FAILED,
            started_at=started_at,
            finished_at=finished_at,
            error_code="worker_interrupted",
            error_summary="Анализ прерван перезапуском обработчика.",
        )


class AnalysisJobStore(Protocol):
    """Хранилище состояния запусков анализа."""

    async def start(self) -> None:
        """Подготавливает внешние ресурсы."""

    async def close(self) -> None:
        """Освобождает внешние ресурсы."""

    async def create(self, job: AnalysisJob) -> AnalysisJob:
        """Сохраняет новое задание в состоянии queued."""

    async def get(self, analysis_id: str) -> AnalysisJob | None:
        """Возвращает задание по идентификатору."""

    async def list_history_for_owner_repositories(
        self,
        owner_subject: str,
        repository_ids: Collection[str],
    ) -> tuple[AnalysisJob, ...]:
        """Возвращает active и последний terminal-запуск владельца на репозиторий."""

    async def mark_running(
        self,
        analysis_id: str,
        started_at: datetime,
        *,
        worker_id: str | None = None,
    ) -> AnalysisJob:
        """Атомарно переводит queued в running для владельца lease."""

    async def finish(
        self,
        analysis_id: str,
        *,
        status: AnalysisJobStatus,
        finished_at: datetime,
        error_code: str | None = None,
        error_summary: str | None = None,
        worker_id: str | None = None,
    ) -> AnalysisJob:
        """Атомарно переводит running в terminal-состояние для владельца lease."""

    async def heartbeat_worker(self, worker_id: str, heartbeat_at: datetime) -> None:
        """Продляет lease активного in-process worker."""

    async def recover_abandoned(
        self,
        *,
        finished_at: datetime,
        stale_before: datetime,
    ) -> tuple[AnalysisJob, ...]:
        """Завершает задания с просроченной lease и legacy-задачи после drain."""


class InMemoryAnalysisJobStore:
    """Потокобезопасное хранилище запусков для модульных и HTTP-тестов."""

    def __init__(self) -> None:
        self._jobs: dict[str, AnalysisJob] = {}
        self._worker_heartbeats: dict[str, datetime] = {}
        self._lock = RLock()

    async def start(self) -> None:
        """Не требует отдельной подготовки."""

    async def close(self) -> None:
        """Не удерживает внешние ресурсы."""

    async def heartbeat_worker(self, worker_id: str, heartbeat_at: datetime) -> None:
        """Сохраняет heartbeat worker для имитации lease в тестах."""

        _require_worker_id(worker_id)
        _require_timezone(heartbeat_at, "heartbeat_at")
        with self._lock:
            self._worker_heartbeats[worker_id] = heartbeat_at

    async def create(self, job: AnalysisJob) -> AnalysisJob:
        if job.status is not AnalysisJobStatus.QUEUED:
            raise AnalysisJobTransitionError("new jobs must be queued")

        with self._lock:
            if job.analysis_id in self._jobs:
                raise ValueError("analysis_id already exists")
            self._jobs[job.analysis_id] = job
            return job

    async def get(self, analysis_id: str) -> AnalysisJob | None:
        with self._lock:
            return self._jobs.get(normalize_analysis_id(analysis_id))

    async def list_history_for_owner_repositories(
        self,
        owner_subject: str,
        repository_ids: Collection[str],
    ) -> tuple[AnalysisJob, ...]:
        _require_owner_subject(owner_subject)
        identifiers = _normalize_repository_ids(repository_ids)
        if not identifiers:
            return ()

        with self._lock:
            latest: dict[tuple[str, str], AnalysisJob] = {}
            for job in self._jobs.values():
                if job.owner_subject != owner_subject or job.repository_id not in identifiers:
                    continue
                key = (job.repository_id, _job_history_group(job))
                previous = latest.get(key)
                if previous is None or _job_order_key(job) > _job_order_key(previous):
                    latest[key] = job
        return tuple(latest[key] for key in sorted(latest))

    async def mark_running(
        self,
        analysis_id: str,
        started_at: datetime,
        *,
        worker_id: str | None = None,
    ) -> AnalysisJob:
        normalized_id = normalize_analysis_id(analysis_id)
        with self._lock:
            job = self._require_job(normalized_id)
            _require_job_owner(job, worker_id)
            updated = job.started(started_at)
            self._jobs[normalized_id] = updated
            return updated

    async def finish(
        self,
        analysis_id: str,
        *,
        status: AnalysisJobStatus,
        finished_at: datetime,
        error_code: str | None = None,
        error_summary: str | None = None,
        worker_id: str | None = None,
    ) -> AnalysisJob:
        normalized_id = normalize_analysis_id(analysis_id)
        with self._lock:
            job = self._require_job(normalized_id)
            _require_job_owner(job, worker_id)
            updated = job.finished(
                status=status,
                finished_at=finished_at,
                error_code=error_code,
                error_summary=error_summary,
            )
            self._jobs[normalized_id] = updated
            return updated

    async def recover_abandoned(
        self,
        *,
        finished_at: datetime,
        stale_before: datetime,
    ) -> tuple[AnalysisJob, ...]:
        """Завершает только задания, владелец которых не продлил lease."""

        _require_timezone(finished_at, "finished_at")
        _require_timezone(stale_before, "stale_before")
        with self._lock:
            recovered = tuple(
                job.interrupted(finished_at)
                for job in self._jobs.values()
                if job.status in {AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING}
                and job.worker_id is not None
                and (
                    self._worker_heartbeats.get(job.worker_id) is None
                    or self._worker_heartbeats[job.worker_id] < stale_before
                )
            )
            self._jobs.update({job.analysis_id: job for job in recovered})
            return recovered

    def _require_job(self, analysis_id: str) -> AnalysisJob:
        job = self._jobs.get(analysis_id)
        if job is None:
            raise AnalysisJobNotFoundError("analysis job not found")
        return job


class PostgresAnalysisJobStore:
    """PostgreSQL-хранилище состояний запусков анализа."""

    def __init__(self, database_url: str) -> None:
        self._database_url = _normalize_database_url(database_url)
        self._pool: asyncpg.Pool | None = None

    @property
    def database_url(self) -> str:
        """Возвращает нормализованный URL базы для проверки конфигурации."""

        return self._database_url

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

    async def create(self, job: AnalysisJob) -> AnalysisJob:
        """Сохраняет новое задание в состоянии queued."""

        if job.status is not AnalysisJobStatus.QUEUED:
            raise AnalysisJobTransitionError("new jobs must be queued")

        try:
            await self._require_pool().execute(
                """
                INSERT INTO analysis_jobs (
                    analysis_id, repository_id, owner_subject, status, created_at,
                    started_at, finished_at, error_code, error_summary, worker_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                job.analysis_id,
                job.repository_id,
                job.owner_subject,
                job.status.value,
                job.created_at,
                job.started_at,
                job.finished_at,
                job.error_code,
                job.error_summary,
                job.worker_id,
            )
        except asyncpg.UniqueViolationError as error:
            raise ValueError("analysis_id already exists") from error
        return job

    async def get(self, analysis_id: str) -> AnalysisJob | None:
        """Читает сохранённое состояние запуска."""

        row = await self._require_pool().fetchrow(
            """
            SELECT
                analysis_id, repository_id, owner_subject, status, created_at,
                started_at, finished_at, error_code, error_summary, worker_id
            FROM analysis_jobs
            WHERE analysis_id = $1
            """,
            normalize_analysis_id(analysis_id),
        )
        return _job_from_row(row) if row is not None else None

    async def list_history_for_owner_repositories(
        self,
        owner_subject: str,
        repository_ids: Collection[str],
    ) -> tuple[AnalysisJob, ...]:
        _require_owner_subject(owner_subject)
        identifiers = _normalize_repository_ids(repository_ids)
        if not identifiers:
            return ()

        rows = await self._require_pool().fetch(
            """
            SELECT
                analysis_id, repository_id, owner_subject, status, created_at,
                started_at, finished_at, error_code, error_summary, worker_id
            FROM (
                SELECT
                    analysis_id, repository_id, owner_subject, status, created_at,
                    started_at, finished_at, error_code, error_summary, worker_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            repository_id,
                            CASE
                                WHEN status IN ('queued', 'running') THEN 'active'
                                ELSE 'terminal'
                            END
                        ORDER BY created_at DESC, analysis_id DESC
                    ) AS row_rank
                FROM analysis_jobs
                WHERE owner_subject = $1 AND repository_id = ANY($2::text[])
            ) AS latest_jobs
            WHERE row_rank = 1
            ORDER BY repository_id
            """,
            owner_subject,
            list(identifiers),
        )
        return tuple(_job_from_row(row) for row in rows)

    async def mark_running(
        self,
        analysis_id: str,
        started_at: datetime,
        *,
        worker_id: str | None = None,
    ) -> AnalysisJob:
        """Переводит queued в running, не перезаписывая lease другого worker."""

        current = await self._require_existing_job(analysis_id)
        _require_job_owner(current, worker_id)
        updated = current.started(started_at)
        row = await self._require_pool().fetchrow(
            """
            UPDATE analysis_jobs
            SET status = $2, started_at = $3
            WHERE analysis_id = $1 AND status = $4
                AND worker_id IS NOT DISTINCT FROM $5
            RETURNING
                analysis_id, repository_id, owner_subject, status, created_at,
                started_at, finished_at, error_code, error_summary, worker_id
            """,
            updated.analysis_id,
            updated.status.value,
            updated.started_at,
            AnalysisJobStatus.QUEUED.value,
            worker_id,
        )
        if row is None:
            raise AnalysisJobTransitionError("job state changed before it could start")
        return _job_from_row(row)

    async def finish(
        self,
        analysis_id: str,
        *,
        status: AnalysisJobStatus,
        finished_at: datetime,
        error_code: str | None = None,
        error_summary: str | None = None,
        worker_id: str | None = None,
    ) -> AnalysisJob:
        """Переводит running в terminal-состояние, не перезаписывая другой lease."""

        current = await self._require_existing_job(analysis_id)
        _require_job_owner(current, worker_id)
        updated = current.finished(
            status=status,
            finished_at=finished_at,
            error_code=error_code,
            error_summary=error_summary,
        )
        row = await self._require_pool().fetchrow(
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
        if row is None:
            raise AnalysisJobTransitionError("job state changed before it could finish")
        return _job_from_row(row)

    async def heartbeat_worker(self, worker_id: str, heartbeat_at: datetime) -> None:
        """Создаёт или продлевает PostgreSQL lease одного worker."""

        _require_worker_id(worker_id)
        _require_timezone(heartbeat_at, "heartbeat_at")
        await self._require_pool().execute(
            """
            INSERT INTO analysis_worker_leases (worker_id, heartbeat_at)
            VALUES ($1, $2)
            ON CONFLICT (worker_id)
            DO UPDATE SET heartbeat_at = EXCLUDED.heartbeat_at
            """,
            worker_id,
            heartbeat_at,
        )

    async def recover_abandoned(
        self,
        *,
        finished_at: datetime,
        stale_before: datetime,
    ) -> tuple[AnalysisJob, ...]:
        """Завершает задания с истёкшей lease и ownerless legacy-задачи после drain."""

        _require_timezone(finished_at, "finished_at")
        _require_timezone(stale_before, "stale_before")
        rows = await self._require_pool().fetch(
            """
            SELECT
                job.analysis_id, job.repository_id, job.owner_subject, job.status,
                job.created_at, job.started_at, job.finished_at, job.error_code,
                job.error_summary, job.worker_id
            FROM analysis_jobs AS job
            LEFT JOIN analysis_worker_leases AS lease
                ON lease.worker_id = job.worker_id
            LEFT JOIN analysis_job_recovery_state AS recovery_state
                ON recovery_state.id = 1
            WHERE job.status = ANY($1::text[])
                AND (
                    (
                        job.worker_id IS NOT NULL
                        AND (lease.worker_id IS NULL OR lease.heartbeat_at < $2)
                    )
                    OR (
                        job.worker_id IS NULL
                        AND recovery_state.ownerless_recovery_after <= $3
                    )
                )
            ORDER BY job.created_at
            """,
            [AnalysisJobStatus.QUEUED.value, AnalysisJobStatus.RUNNING.value],
            stale_before,
            finished_at,
        )
        recovered: list[AnalysisJob] = []
        for row in rows:
            current = _job_from_row(row)
            updated = current.interrupted(finished_at)
            saved = await self._require_pool().fetchrow(
                """
                UPDATE analysis_jobs AS job
                SET
                    status = $2,
                    started_at = $3,
                    finished_at = $4,
                    error_code = $5,
                    error_summary = $6
                WHERE job.analysis_id = $1 AND job.status = $7
                    AND job.worker_id IS NOT DISTINCT FROM $8
                    AND (
                        (
                            job.worker_id IS NOT NULL
                            AND NOT EXISTS (
                                SELECT 1
                                FROM analysis_worker_leases AS current_lease
                                WHERE current_lease.worker_id = job.worker_id
                                    AND current_lease.heartbeat_at >= $9
                            )
                        )
                        OR (
                            job.worker_id IS NULL
                            AND EXISTS (
                                SELECT 1
                                FROM analysis_job_recovery_state AS recovery_state
                                WHERE recovery_state.id = 1
                                    AND recovery_state.ownerless_recovery_after <= $10
                            )
                        )
                    )
                RETURNING
                analysis_id, repository_id, owner_subject, status, created_at,
                started_at, finished_at, error_code, error_summary, worker_id
                """,
                updated.analysis_id,
                updated.status.value,
                updated.started_at,
                updated.finished_at,
                updated.error_code,
                updated.error_summary,
                current.status.value,
                current.worker_id,
                stale_before,
                finished_at,
            )
            if saved is not None:
                recovered.append(_job_from_row(saved))
        return tuple(recovered)

    async def _require_existing_job(self, analysis_id: str) -> AnalysisJob:
        job = await self.get(analysis_id)
        if job is None:
            raise AnalysisJobNotFoundError("analysis job not found")
        return job

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Analysis job store is not started.")
        return self._pool


def _require_owner_subject(owner_subject: str) -> None:
    if (
        not isinstance(owner_subject, str)
        or not owner_subject
        or owner_subject != owner_subject.strip()
    ):
        raise ValueError("owner_subject must not be empty")


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


def _job_order_key(job: AnalysisJob) -> tuple[datetime, str]:
    return job.created_at, job.analysis_id


def _job_history_group(job: AnalysisJob) -> str:
    if job.status in {AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING}:
        return "active"
    return "terminal"


def _require_job_owner(job: AnalysisJob, worker_id: str | None) -> None:
    if job.worker_id != worker_id:
        raise AnalysisJobTransitionError("job belongs to another worker")


def _require_worker_id(worker_id: str) -> None:
    if not worker_id.strip():
        raise ValueError("worker_id must not be empty")


def _require_timezone(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _validate_job_state(job: AnalysisJob) -> None:
    if job.status is AnalysisJobStatus.QUEUED:
        if any((job.started_at, job.finished_at, job.error_code, job.error_summary)):
            raise ValueError("queued jobs must not have execution fields")
        return

    if job.status is AnalysisJobStatus.RUNNING:
        if job.started_at is None or any((job.finished_at, job.error_code, job.error_summary)):
            raise ValueError("running jobs require started_at and no terminal fields")
        _require_timezone(job.started_at, "started_at")
        if job.started_at < job.created_at:
            raise ValueError("started_at must not be before created_at")
        return

    if job.started_at is None or job.finished_at is None:
        raise ValueError("terminal jobs require started_at and finished_at")
    _require_timezone(job.started_at, "started_at")
    _require_timezone(job.finished_at, "finished_at")
    if job.started_at < job.created_at or job.finished_at < job.started_at:
        raise ValueError("job timestamps must be chronological")

    if job.status is AnalysisJobStatus.FAILED:
        if not job.error_code or not job.error_summary:
            raise ValueError("failed jobs require an error code and summary")
    elif job.error_code is not None or job.error_summary is not None:
        raise ValueError("successful jobs must not contain error details")


def _job_from_row(row: asyncpg.Record) -> AnalysisJob:
    return AnalysisJob(
        analysis_id=str(row["analysis_id"]),
        repository_id=str(row["repository_id"]),
        status=AnalysisJobStatus(str(row["status"])),
        created_at=row["created_at"],
        owner_subject=row["owner_subject"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_code=row["error_code"],
        error_summary=row["error_summary"],
        worker_id=row["worker_id"],
    )


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+asyncpg://")
    return database_url
