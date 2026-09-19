"""Состояния запусков анализа и их постоянное хранение."""

from __future__ import annotations

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
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = None

    def __post_init__(self) -> None:
        if self.analysis_id != normalize_analysis_id(self.analysis_id):
            raise ValueError("analysis_id must not contain surrounding whitespace")
        if not self.repository_id.strip():
            raise ValueError("repository_id must not be empty")
        _require_timezone(self.created_at, "created_at")
        _validate_job_state(self)

    @classmethod
    def queued(
        cls,
        *,
        analysis_id: str,
        repository_id: str,
        created_at: datetime,
    ) -> AnalysisJob:
        """Создаёт задание, ожидающее обработчик."""

        return cls(
            analysis_id=normalize_analysis_id(analysis_id),
            repository_id=repository_id.strip(),
            status=AnalysisJobStatus.QUEUED,
            created_at=created_at,
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

    async def mark_running(self, analysis_id: str, started_at: datetime) -> AnalysisJob:
        """Атомарно переводит queued в running."""

    async def finish(
        self,
        analysis_id: str,
        *,
        status: AnalysisJobStatus,
        finished_at: datetime,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> AnalysisJob:
        """Атомарно переводит running в terminal-состояние."""


class InMemoryAnalysisJobStore:
    """Потокобезопасное хранилище запусков для модульных и HTTP-тестов."""

    def __init__(self) -> None:
        self._jobs: dict[str, AnalysisJob] = {}
        self._lock = RLock()

    async def start(self) -> None:
        """Не требует отдельной подготовки."""

    async def close(self) -> None:
        """Не удерживает внешние ресурсы."""

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

    async def mark_running(self, analysis_id: str, started_at: datetime) -> AnalysisJob:
        normalized_id = normalize_analysis_id(analysis_id)
        with self._lock:
            job = self._require_job(normalized_id)
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
    ) -> AnalysisJob:
        normalized_id = normalize_analysis_id(analysis_id)
        with self._lock:
            job = self._require_job(normalized_id)
            updated = job.finished(
                status=status,
                finished_at=finished_at,
                error_code=error_code,
                error_summary=error_summary,
            )
            self._jobs[normalized_id] = updated
            return updated

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

    async def start(self) -> None:
        """Открывает пул и создаёт таблицу MVP для запусков анализа."""

        if self._pool is not None:
            return

        self._pool = await asyncpg.create_pool(self._database_url)
        await self._pool.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                analysis_id TEXT PRIMARY KEY,
                repository_id TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ('queued', 'running', 'completed', 'partial', 'failed')),
                created_at TIMESTAMPTZ NOT NULL,
                started_at TIMESTAMPTZ NULL,
                finished_at TIMESTAMPTZ NULL,
                error_code TEXT NULL,
                error_summary TEXT NULL
            )
            """
        )

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
                    analysis_id, repository_id, status, created_at,
                    started_at, finished_at, error_code, error_summary
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                job.analysis_id,
                job.repository_id,
                job.status.value,
                job.created_at,
                job.started_at,
                job.finished_at,
                job.error_code,
                job.error_summary,
            )
        except asyncpg.UniqueViolationError as error:
            raise ValueError("analysis_id already exists") from error
        return job

    async def get(self, analysis_id: str) -> AnalysisJob | None:
        """Читает сохранённое состояние запуска."""

        row = await self._require_pool().fetchrow(
            """
            SELECT
                analysis_id, repository_id, status, created_at,
                started_at, finished_at, error_code, error_summary
            FROM analysis_jobs
            WHERE analysis_id = $1
            """,
            normalize_analysis_id(analysis_id),
        )
        return _job_from_row(row) if row is not None else None

    async def mark_running(self, analysis_id: str, started_at: datetime) -> AnalysisJob:
        """Переводит queued в running, не перезаписывая другой переход."""

        current = await self._require_existing_job(analysis_id)
        updated = current.started(started_at)
        row = await self._require_pool().fetchrow(
            """
            UPDATE analysis_jobs
            SET status = $2, started_at = $3
            WHERE analysis_id = $1 AND status = $4
            RETURNING
                analysis_id, repository_id, status, created_at,
                started_at, finished_at, error_code, error_summary
            """,
            updated.analysis_id,
            updated.status.value,
            updated.started_at,
            AnalysisJobStatus.QUEUED.value,
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
    ) -> AnalysisJob:
        """Переводит running в terminal-состояние, не перезаписывая другой переход."""

        current = await self._require_existing_job(analysis_id)
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
            RETURNING
                analysis_id, repository_id, status, created_at,
                started_at, finished_at, error_code, error_summary
            """,
            updated.analysis_id,
            updated.status.value,
            updated.finished_at,
            updated.error_code,
            updated.error_summary,
            AnalysisJobStatus.RUNNING.value,
        )
        if row is None:
            raise AnalysisJobTransitionError("job state changed before it could finish")
        return _job_from_row(row)

    async def _require_existing_job(self, analysis_id: str) -> AnalysisJob:
        job = await self.get(analysis_id)
        if job is None:
            raise AnalysisJobNotFoundError("analysis job not found")
        return job

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Analysis job store is not started.")
        return self._pool


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
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_code=row["error_code"],
        error_summary=row["error_summary"],
    )


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+asyncpg://")
    return database_url
