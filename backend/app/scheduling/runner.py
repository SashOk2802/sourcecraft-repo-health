"""Периодический пересчёт только подтверждённых public-репозиториев.

Планировщик не принимает HTTP-запросы и не использует токены пользователей.
Он читает уже ограниченный public-каталог SourceCraft, создаёт задания через
обычный dispatcher и хранит момент следующей проверки отдельно от отчётов.
Несколько экземпляров приложения координируются lease в PostgreSQL.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Protocol
from uuid import uuid4

import asyncpg

from backend.app.analysis.dispatch import AnalysisDispatcher, AnalysisPrincipal
from backend.app.analysis.jobs import AnalysisJobStatus, AnalysisJobStore
from backend.app.integrations.sourcecraft_public_catalog import PublicRepositoryCatalog
from backend.app.scheduling.policy import (
    schedule_periodic_analysis,
    schedule_temporary_retry,
)

logger = logging.getLogger(__name__)

SYSTEM_SCHEDULER_SUBJECT = "system:public-scheduler"
_DEFAULT_SCAN_INTERVAL = timedelta(minutes=1)
_DEFAULT_ENTRY_LEASE = timedelta(minutes=2)
_DEFAULT_BATCH_SIZE = 10


@dataclass(frozen=True, slots=True)
class ScheduleCandidate:
    """Публичный репозиторий и известная каталогу дата его активности."""

    repository_id: str
    last_activity_at: datetime | None

    def __post_init__(self) -> None:
        _require_repository_id(self.repository_id)
        if self.last_activity_at is not None:
            _require_aware(self.last_activity_at, "last_activity_at")


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    """Сохранённое состояние расписания одного public-репозитория."""

    repository_id: str
    last_activity_at: datetime | None
    next_analysis_at: datetime
    in_flight_analysis_id: str | None
    consecutive_failures: int
    active: bool
    updated_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_repository_id(self.repository_id)
        if self.last_activity_at is not None:
            _require_aware(self.last_activity_at, "last_activity_at")
        _require_aware(self.next_analysis_at, "next_analysis_at")
        _require_aware(self.updated_at, "updated_at")
        if (
            isinstance(self.consecutive_failures, bool)
            or not isinstance(self.consecutive_failures, int)
            or self.consecutive_failures < 0
        ):
            raise ValueError("consecutive_failures must be a non-negative integer")
        if self.in_flight_analysis_id is not None and not self.in_flight_analysis_id.strip():
            raise ValueError("in_flight_analysis_id must not be blank")
        if self.lease_owner is None and self.lease_expires_at is not None:
            raise ValueError("lease_expires_at requires lease_owner")
        if self.lease_owner is not None:
            if not self.lease_owner.strip():
                raise ValueError("lease_owner must not be blank")
            if self.lease_expires_at is None:
                raise ValueError("lease_owner requires lease_expires_at")
            _require_aware(self.lease_expires_at, "lease_expires_at")


@dataclass(frozen=True, slots=True)
class SchedulerRun:
    """Наблюдаемый итог одного прохода без раскрытия репозиториев."""

    catalog_size: int = 0
    reconciled: int = 0
    submitted: int = 0
    deferred: int = 0


class AnalysisScheduleStore(Protocol):
    """Постоянное расписание public-анализов."""

    async def start(self) -> None:
        """Подготавливает внешние ресурсы."""

    async def close(self) -> None:
        """Освобождает внешние ресурсы."""

    async def reconcile_catalog(
        self,
        candidates: Iterable[ScheduleCandidate],
        *,
        observed_at: datetime,
    ) -> None:
        """Добавляет текущий public-каталог и выключает исчезнувшие репозитории."""

    async def list_in_flight(self) -> tuple[ScheduleEntry, ...]:
        """Возвращает задания, результат которых ещё не учтён в расписании."""

    async def claim_due(
        self,
        *,
        now: datetime,
        limit: int,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> tuple[ScheduleEntry, ...]:
        """Атомарно закрепляет ограниченное число просроченных записей."""

    async def record_submission(
        self,
        repository_id: str,
        *,
        lease_owner: str,
        analysis_id: str,
        updated_at: datetime,
    ) -> bool:
        """Связывает подтверждённую lease с созданным заданием."""

    async def release_submission(
        self,
        repository_id: str,
        *,
        lease_owner: str,
        next_analysis_at: datetime,
        consecutive_failures: int,
        updated_at: datetime,
    ) -> bool:
        """Освобождает lease после ошибки постановки и назначает повтор."""

    async def settle(
        self,
        repository_id: str,
        *,
        analysis_id: str,
        next_analysis_at: datetime,
        consecutive_failures: int,
        updated_at: datetime,
    ) -> bool:
        """Учитывает terminal-результат задания и назначает следующий запуск."""


class InMemoryAnalysisScheduleStore:
    """Потокобезопасное расписание для тестов и локального запуска без PostgreSQL."""

    def __init__(self) -> None:
        self._entries: dict[str, ScheduleEntry] = {}
        self._lock = RLock()

    async def start(self) -> None:
        """В памяти нет внешнего ресурса."""

    async def close(self) -> None:
        """В памяти нет внешнего ресурса."""

    async def reconcile_catalog(
        self,
        candidates: Iterable[ScheduleCandidate],
        *,
        observed_at: datetime,
    ) -> None:
        _require_aware(observed_at, "observed_at")
        items = _unique_candidates(candidates)
        identifiers = {item.repository_id for item in items}
        with self._lock:
            for identifier, entry in tuple(self._entries.items()):
                if identifier not in identifiers and entry.active:
                    self._entries[identifier] = replace(entry, active=False, updated_at=observed_at)
            for candidate in items:
                previous = self._entries.get(candidate.repository_id)
                if previous is None:
                    self._entries[candidate.repository_id] = ScheduleEntry(
                        repository_id=candidate.repository_id,
                        last_activity_at=candidate.last_activity_at,
                        next_analysis_at=observed_at,
                        in_flight_analysis_id=None,
                        consecutive_failures=0,
                        active=True,
                        updated_at=observed_at,
                    )
                else:
                    self._entries[candidate.repository_id] = replace(
                        previous,
                        last_activity_at=candidate.last_activity_at,
                        active=True,
                        updated_at=observed_at,
                    )

    async def list_in_flight(self) -> tuple[ScheduleEntry, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        entry
                        for entry in self._entries.values()
                        if entry.in_flight_analysis_id is not None
                    ),
                    key=lambda entry: entry.repository_id,
                )
            )

    async def claim_due(
        self,
        *,
        now: datetime,
        limit: int,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> tuple[ScheduleEntry, ...]:
        _require_aware(now, "now")
        _require_aware(lease_expires_at, "lease_expires_at")
        _require_positive_limit(limit)
        _require_lease_owner(lease_owner)
        if lease_expires_at <= now:
            raise ValueError("lease_expires_at must be later than now")

        with self._lock:
            due = sorted(
                (
                    entry
                    for entry in self._entries.values()
                    if entry.active
                    and entry.in_flight_analysis_id is None
                    and entry.next_analysis_at <= now
                    and (
                        entry.lease_expires_at is None
                        or entry.lease_expires_at <= now
                    )
                ),
                key=lambda entry: (entry.next_analysis_at, entry.repository_id),
            )[:limit]
            claimed = tuple(
                replace(
                    entry,
                    lease_owner=lease_owner,
                    lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
                for entry in due
            )
            self._entries.update({entry.repository_id: entry for entry in claimed})
            return claimed

    async def record_submission(
        self,
        repository_id: str,
        *,
        lease_owner: str,
        analysis_id: str,
        updated_at: datetime,
    ) -> bool:
        _require_repository_id(repository_id)
        _require_lease_owner(lease_owner)
        _require_analysis_id(analysis_id)
        _require_aware(updated_at, "updated_at")
        with self._lock:
            entry = self._entries.get(repository_id)
            if (
                entry is None
                or not entry.active
                or entry.in_flight_analysis_id is not None
                or entry.lease_owner != lease_owner
            ):
                return False
            self._entries[repository_id] = replace(
                entry,
                in_flight_analysis_id=analysis_id,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=updated_at,
            )
            return True

    async def release_submission(
        self,
        repository_id: str,
        *,
        lease_owner: str,
        next_analysis_at: datetime,
        consecutive_failures: int,
        updated_at: datetime,
    ) -> bool:
        _require_repository_id(repository_id)
        _require_lease_owner(lease_owner)
        _require_aware(next_analysis_at, "next_analysis_at")
        _require_failures(consecutive_failures)
        _require_aware(updated_at, "updated_at")
        with self._lock:
            entry = self._entries.get(repository_id)
            if (
                entry is None
                or entry.in_flight_analysis_id is not None
                or entry.lease_owner != lease_owner
            ):
                return False
            self._entries[repository_id] = replace(
                entry,
                next_analysis_at=next_analysis_at,
                consecutive_failures=consecutive_failures,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=updated_at,
            )
            return True

    async def settle(
        self,
        repository_id: str,
        *,
        analysis_id: str,
        next_analysis_at: datetime,
        consecutive_failures: int,
        updated_at: datetime,
    ) -> bool:
        _require_repository_id(repository_id)
        _require_analysis_id(analysis_id)
        _require_aware(next_analysis_at, "next_analysis_at")
        _require_failures(consecutive_failures)
        _require_aware(updated_at, "updated_at")
        with self._lock:
            entry = self._entries.get(repository_id)
            if entry is None or entry.in_flight_analysis_id != analysis_id:
                return False
            self._entries[repository_id] = replace(
                entry,
                next_analysis_at=next_analysis_at,
                in_flight_analysis_id=None,
                consecutive_failures=consecutive_failures,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=updated_at,
            )
            return True


class PostgresAnalysisScheduleStore:
    """Расписание в PostgreSQL с lease, безопасной для нескольких экземпляров."""

    def __init__(self, database_url: str) -> None:
        self._database_url = _normalize_database_url(database_url)
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._database_url)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def reconcile_catalog(
        self,
        candidates: Iterable[ScheduleCandidate],
        *,
        observed_at: datetime,
    ) -> None:
        _require_aware(observed_at, "observed_at")
        items = _unique_candidates(candidates)
        identifiers = [item.repository_id for item in items]
        pool = self._require_pool()
        async with pool.acquire() as connection, connection.transaction():
            if identifiers:
                await connection.execute(
                    """
                    UPDATE analysis_schedules
                    SET active = FALSE, updated_at = $2
                    WHERE active = TRUE
                        AND NOT (repository_id = ANY($1::text[]))
                    """,
                    identifiers,
                    observed_at,
                )
            else:
                await connection.execute(
                    "UPDATE analysis_schedules SET active = FALSE, updated_at = $1 WHERE active = TRUE",
                    observed_at,
                )
            for item in items:
                await connection.execute(
                    """
                    INSERT INTO analysis_schedules (
                        repository_id, last_activity_at, next_analysis_at,
                        in_flight_analysis_id, consecutive_failures, active,
                        updated_at, lease_owner, lease_expires_at
                    )
                    VALUES ($1, $2, $3, NULL, 0, TRUE, $3, NULL, NULL)
                    ON CONFLICT (repository_id)
                    DO UPDATE SET
                        last_activity_at = EXCLUDED.last_activity_at,
                        active = TRUE,
                        updated_at = EXCLUDED.updated_at
                    """,
                    item.repository_id,
                    item.last_activity_at,
                    observed_at,
                )

    async def list_in_flight(self) -> tuple[ScheduleEntry, ...]:
        rows = await self._require_pool().fetch(
            """
            SELECT repository_id, last_activity_at, next_analysis_at,
                in_flight_analysis_id, consecutive_failures, active, updated_at,
                lease_owner, lease_expires_at
            FROM analysis_schedules
            WHERE in_flight_analysis_id IS NOT NULL
            ORDER BY repository_id
            """
        )
        return tuple(_entry_from_row(row) for row in rows)

    async def claim_due(
        self,
        *,
        now: datetime,
        limit: int,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> tuple[ScheduleEntry, ...]:
        _require_aware(now, "now")
        _require_aware(lease_expires_at, "lease_expires_at")
        _require_positive_limit(limit)
        _require_lease_owner(lease_owner)
        if lease_expires_at <= now:
            raise ValueError("lease_expires_at must be later than now")

        rows = await self._require_pool().fetch(
            """
            WITH due AS (
                SELECT repository_id
                FROM analysis_schedules
                WHERE active = TRUE
                    AND in_flight_analysis_id IS NULL
                    AND next_analysis_at <= $1
                    AND (lease_expires_at IS NULL OR lease_expires_at <= $1)
                ORDER BY next_analysis_at, repository_id
                FOR UPDATE SKIP LOCKED
                LIMIT $2
            )
            UPDATE analysis_schedules AS schedule
            SET
                lease_owner = $3,
                lease_expires_at = $4,
                updated_at = $1
            FROM due
            WHERE schedule.repository_id = due.repository_id
            RETURNING
                schedule.repository_id, schedule.last_activity_at,
                schedule.next_analysis_at, schedule.in_flight_analysis_id,
                schedule.consecutive_failures, schedule.active, schedule.updated_at,
                schedule.lease_owner, schedule.lease_expires_at
            """,
            now,
            limit,
            lease_owner,
            lease_expires_at,
        )
        return tuple(_entry_from_row(row) for row in rows)

    async def record_submission(
        self,
        repository_id: str,
        *,
        lease_owner: str,
        analysis_id: str,
        updated_at: datetime,
    ) -> bool:
        _require_repository_id(repository_id)
        _require_lease_owner(lease_owner)
        _require_analysis_id(analysis_id)
        _require_aware(updated_at, "updated_at")
        result = await self._require_pool().execute(
            """
            UPDATE analysis_schedules
            SET
                in_flight_analysis_id = $3,
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = $4
            WHERE repository_id = $1
                AND active = TRUE
                AND in_flight_analysis_id IS NULL
                AND lease_owner = $2
            """,
            repository_id,
            lease_owner,
            analysis_id,
            updated_at,
        )
        return _was_updated(result)

    async def release_submission(
        self,
        repository_id: str,
        *,
        lease_owner: str,
        next_analysis_at: datetime,
        consecutive_failures: int,
        updated_at: datetime,
    ) -> bool:
        _require_repository_id(repository_id)
        _require_lease_owner(lease_owner)
        _require_aware(next_analysis_at, "next_analysis_at")
        _require_failures(consecutive_failures)
        _require_aware(updated_at, "updated_at")
        result = await self._require_pool().execute(
            """
            UPDATE analysis_schedules
            SET
                next_analysis_at = $3,
                consecutive_failures = $4,
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = $5
            WHERE repository_id = $1
                AND in_flight_analysis_id IS NULL
                AND lease_owner = $2
            """,
            repository_id,
            lease_owner,
            next_analysis_at,
            consecutive_failures,
            updated_at,
        )
        return _was_updated(result)

    async def settle(
        self,
        repository_id: str,
        *,
        analysis_id: str,
        next_analysis_at: datetime,
        consecutive_failures: int,
        updated_at: datetime,
    ) -> bool:
        _require_repository_id(repository_id)
        _require_analysis_id(analysis_id)
        _require_aware(next_analysis_at, "next_analysis_at")
        _require_failures(consecutive_failures)
        _require_aware(updated_at, "updated_at")
        result = await self._require_pool().execute(
            """
            UPDATE analysis_schedules
            SET
                next_analysis_at = $3,
                in_flight_analysis_id = NULL,
                consecutive_failures = $4,
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = $5
            WHERE repository_id = $1 AND in_flight_analysis_id = $2
            """,
            repository_id,
            analysis_id,
            next_analysis_at,
            consecutive_failures,
            updated_at,
        )
        return _was_updated(result)

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Analysis schedule store is not started.")
        return self._pool


class PublicAnalysisScheduler:
    """Регулярно ставит public-анализы, не обходя проверки dispatcher."""

    def __init__(
        self,
        *,
        repository_catalog: PublicRepositoryCatalog,
        dispatcher: AnalysisDispatcher,
        job_store: AnalysisJobStore,
        schedule_store: AnalysisScheduleStore,
        clock: Callable[[], datetime] | None = None,
        scan_interval: timedelta = _DEFAULT_SCAN_INTERVAL,
        entry_lease: timedelta = _DEFAULT_ENTRY_LEASE,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        scheduler_id: str | None = None,
    ) -> None:
        if scan_interval <= timedelta():
            raise ValueError("scan_interval must be positive")
        if entry_lease <= timedelta():
            raise ValueError("entry_lease must be positive")
        _require_positive_limit(batch_size)
        self._repository_catalog = repository_catalog
        self._dispatcher = dispatcher
        self._job_store = job_store
        self._schedule_store = schedule_store
        self._clock = clock or _utc_now
        self._scan_interval = scan_interval
        self._entry_lease = entry_lease
        self._batch_size = batch_size
        self._scheduler_id = scheduler_id or f"public-scheduler-{uuid4().hex}"
        _require_lease_owner(self._scheduler_id)
        self._task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        """Делает один проход сразу, затем запускает редкий фоновый цикл."""

        async with self._start_lock:
            if self._task is not None:
                return
            await self._schedule_store.start()
            await self.run_once()
            self._task = asyncio.create_task(
                self._run_forever(),
                name=f"public-analysis-scheduler-{self._scheduler_id}",
            )

    async def close(self) -> None:
        """Останавливает только свой цикл; уже созданные анализы завершает dispatcher."""

        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._schedule_store.close()

    async def run_once(self) -> SchedulerRun:
        """Сверяет каталог, учитывает terminal-запуски и ставит ограниченную пачку."""

        now = _as_utc(self._clock(), "clock")
        try:
            repositories = await self._repository_catalog.list_repositories()
            candidates = tuple(
                ScheduleCandidate(
                    repository_id=repository.id,
                    last_activity_at=repository.last_activity_at,
                )
                for repository in repositories
            )
            await self._schedule_store.reconcile_catalog(candidates, observed_at=now)
            reconciled = await self._reconcile_terminal_jobs(
                now=now,
                candidates={candidate.repository_id: candidate for candidate in candidates},
            )
            claimed = await self._schedule_store.claim_due(
                now=now,
                limit=self._batch_size,
                lease_owner=self._scheduler_id,
                lease_expires_at=now + self._entry_lease,
            )
        except Exception:
            logger.exception("Не удалось подготовить периодический public-анализ.")
            return SchedulerRun()

        submitted = 0
        deferred = 0
        principal = AnalysisPrincipal(SYSTEM_SCHEDULER_SUBJECT)
        for entry in claimed:
            try:
                job = await self._dispatcher.submit(entry.repository_id, principal)
                if job.repository_id != entry.repository_id:
                    raise RuntimeError("dispatcher returned a job for another repository")
                saved = await self._schedule_store.record_submission(
                    entry.repository_id,
                    lease_owner=self._scheduler_id,
                    analysis_id=job.analysis_id,
                    updated_at=now,
                )
                if not saved:
                    logger.error(
                        "Периодическое задание создано, но не привязано к расписанию.",
                        extra={"analysis_id": job.analysis_id},
                    )
                submitted += 1
            except Exception:
                logger.exception("Не удалось поставить периодический public-анализ.")
                retry = schedule_temporary_retry(
                    entry.repository_id,
                    failed_at=now,
                    consecutive_failures=entry.consecutive_failures + 1,
                )
                released = await self._schedule_store.release_submission(
                    entry.repository_id,
                    lease_owner=self._scheduler_id,
                    next_analysis_at=retry.due_at,
                    consecutive_failures=retry.consecutive_failures,
                    updated_at=now,
                )
                if released:
                    deferred += 1

        return SchedulerRun(
            catalog_size=len(candidates),
            reconciled=reconciled,
            submitted=submitted,
            deferred=deferred,
        )

    async def _reconcile_terminal_jobs(
        self,
        *,
        now: datetime,
        candidates: dict[str, ScheduleCandidate],
    ) -> int:
        settled = 0
        for entry in await self._schedule_store.list_in_flight():
            analysis_id = entry.in_flight_analysis_id
            if analysis_id is None:
                continue
            job = await self._job_store.get(analysis_id)
            if job is None:
                retry = schedule_temporary_retry(
                    entry.repository_id,
                    failed_at=now,
                    consecutive_failures=entry.consecutive_failures + 1,
                )
                changed = await self._schedule_store.settle(
                    entry.repository_id,
                    analysis_id=analysis_id,
                    next_analysis_at=retry.due_at,
                    consecutive_failures=retry.consecutive_failures,
                    updated_at=now,
                )
            elif job.status in {AnalysisJobStatus.COMPLETED, AnalysisJobStatus.PARTIAL}:
                scheduled_from = job.finished_at or now
                candidate = candidates.get(entry.repository_id)
                periodic = schedule_periodic_analysis(
                    entry.repository_id,
                    scheduled_from=scheduled_from,
                    last_activity_at=(
                        candidate.last_activity_at
                        if candidate is not None
                        else entry.last_activity_at
                    ),
                )
                changed = await self._schedule_store.settle(
                    entry.repository_id,
                    analysis_id=analysis_id,
                    next_analysis_at=periodic.due_at,
                    consecutive_failures=0,
                    updated_at=now,
                )
            elif job.status is AnalysisJobStatus.FAILED:
                retry = schedule_temporary_retry(
                    entry.repository_id,
                    failed_at=job.finished_at or now,
                    consecutive_failures=entry.consecutive_failures + 1,
                )
                changed = await self._schedule_store.settle(
                    entry.repository_id,
                    analysis_id=analysis_id,
                    next_analysis_at=retry.due_at,
                    consecutive_failures=retry.consecutive_failures,
                    updated_at=now,
                )
            else:
                continue
            settled += int(changed)
        return settled

    async def _run_forever(self) -> None:
        while True:
            await asyncio.sleep(self._scan_interval.total_seconds())
            await self.run_once()


def _unique_candidates(candidates: Iterable[ScheduleCandidate]) -> tuple[ScheduleCandidate, ...]:
    items = tuple(candidates)
    identifiers = [item.repository_id for item in items]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("public repository catalog contains duplicate identifiers")
    return items


def _entry_from_row(row: asyncpg.Record) -> ScheduleEntry:
    return ScheduleEntry(
        repository_id=str(row["repository_id"]),
        last_activity_at=row["last_activity_at"],
        next_analysis_at=row["next_analysis_at"],
        in_flight_analysis_id=row["in_flight_analysis_id"],
        consecutive_failures=int(row["consecutive_failures"]),
        active=bool(row["active"]),
        updated_at=row["updated_at"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
    )


def _was_updated(command_status: str) -> bool:
    return command_status.endswith(" 1")


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+asyncpg://")
    return database_url


def _require_repository_id(repository_id: str) -> None:
    if not isinstance(repository_id, str) or not repository_id.strip():
        raise ValueError("repository_id must not be blank")


def _require_analysis_id(analysis_id: str) -> None:
    if not isinstance(analysis_id, str) or not analysis_id.strip():
        raise ValueError("analysis_id must not be blank")


def _require_lease_owner(lease_owner: str) -> None:
    if not isinstance(lease_owner, str) or not lease_owner.strip():
        raise ValueError("lease_owner must not be blank")


def _require_positive_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")


def _require_failures(consecutive_failures: int) -> None:
    if (
        isinstance(consecutive_failures, bool)
        or not isinstance(consecutive_failures, int)
        or consecutive_failures < 0
    ):
        raise ValueError("consecutive_failures must be a non-negative integer")


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _as_utc(value: datetime, field_name: str) -> datetime:
    _require_aware(value, field_name)
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)
