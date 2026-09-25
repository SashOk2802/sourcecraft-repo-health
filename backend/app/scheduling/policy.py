"""Детерминированная политика регулярного пересчёта без доступа к сети и БД."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

ACTIVE_ACTIVITY_WINDOW = timedelta(days=7)
DORMANT_ACTIVITY_WINDOW = timedelta(days=90)
ACTIVE_ANALYSIS_INTERVAL = timedelta(hours=6)
STANDARD_ANALYSIS_INTERVAL = timedelta(hours=24)
DORMANT_ANALYSIS_INTERVAL = timedelta(hours=72)
INITIAL_RETRY_DELAY = timedelta(minutes=15)
MAX_RETRY_DELAY = timedelta(hours=6)
MANUAL_ANALYSIS_INTERVAL = timedelta(minutes=15)
_JITTER_RATIO = 0.1


class AnalysisCadence(StrEnum):
    """Группа репозитория для периодического пересчёта."""

    ACTIVE = "active"
    STANDARD = "standard"
    DORMANT = "dormant"


@dataclass(frozen=True, slots=True)
class PeriodicSchedule:
    """Решение о следующем штатном запуске одного репозитория."""

    cadence: AnalysisCadence
    interval: timedelta
    jitter: timedelta
    due_at: datetime


@dataclass(frozen=True, slots=True)
class RetrySchedule:
    """Решение о повторе после временной ошибки источника."""

    consecutive_failures: int
    delay: timedelta
    jitter: timedelta
    due_at: datetime


@dataclass(frozen=True, slots=True)
class ManualRequestDecision:
    """Результат ограничения частоты ручного запуска."""

    accepted: bool
    next_allowed_at: datetime


def schedule_periodic_analysis(
    repository_id: str,
    *,
    scheduled_from: datetime,
    last_activity_at: datetime | None,
) -> PeriodicSchedule:
    """Назначает следующий запуск после завершённого анализа.

    Небольшой детерминированный jitter разводит репозитории по времени, не делая
    их проверку чаще выбранного интервала. Если дата активности неизвестна,
    используется стандартная частота: неизвестность не считается неактивностью.
    """

    repository_key = _repository_key(repository_id)
    _require_aware(scheduled_from, "scheduled_from")
    if last_activity_at is not None:
        _require_aware(last_activity_at, "last_activity_at")
        if last_activity_at > scheduled_from:
            raise ValueError("last_activity_at must not be later than scheduled_from")

    cadence, interval = _cadence_for(last_activity_at, scheduled_from)
    jitter = _positive_jitter(repository_key, interval, purpose="periodic")
    return PeriodicSchedule(
        cadence=cadence,
        interval=interval,
        jitter=jitter,
        due_at=scheduled_from + interval + jitter,
    )


def schedule_temporary_retry(
    repository_id: str,
    *,
    failed_at: datetime,
    consecutive_failures: int,
) -> RetrySchedule:
    """Назначает повтор временной ошибки с экспоненциальной задержкой.

    Счётчик ведёт вызывающий worker: после успешного запуска он сбрасывается.
    Задержка не превышает шести часов, а jitter добавляется только пока не
    достигнут этот предел.
    """

    repository_key = _repository_key(repository_id)
    _require_aware(failed_at, "failed_at")
    if (
        isinstance(consecutive_failures, bool)
        or not isinstance(consecutive_failures, int)
        or consecutive_failures < 1
    ):
        raise ValueError("consecutive_failures must be a positive integer")

    delay = (
        MAX_RETRY_DELAY
        if consecutive_failures >= 6
        else INITIAL_RETRY_DELAY * 2 ** (consecutive_failures - 1)
    )
    remaining_before_limit = MAX_RETRY_DELAY - delay
    jitter = min(
        _positive_jitter(repository_key, delay, purpose=f"retry:{consecutive_failures}"),
        remaining_before_limit,
    )
    return RetrySchedule(
        consecutive_failures=consecutive_failures,
        delay=delay,
        jitter=jitter,
        due_at=failed_at + delay + jitter,
    )


def decide_manual_request(
    *,
    requested_at: datetime,
    previous_request_at: datetime | None,
) -> ManualRequestDecision:
    """Ограничивает повторный ручной запуск одним запросом за 15 минут."""

    _require_aware(requested_at, "requested_at")
    if previous_request_at is None:
        return ManualRequestDecision(
            accepted=True,
            next_allowed_at=requested_at + MANUAL_ANALYSIS_INTERVAL,
        )

    _require_aware(previous_request_at, "previous_request_at")
    if previous_request_at > requested_at:
        raise ValueError("previous_request_at must not be later than requested_at")
    earliest_allowed_at = previous_request_at + MANUAL_ANALYSIS_INTERVAL
    if requested_at < earliest_allowed_at:
        return ManualRequestDecision(accepted=False, next_allowed_at=earliest_allowed_at)
    return ManualRequestDecision(
        accepted=True,
        next_allowed_at=requested_at + MANUAL_ANALYSIS_INTERVAL,
    )


def _cadence_for(
    last_activity_at: datetime | None,
    scheduled_from: datetime,
) -> tuple[AnalysisCadence, timedelta]:
    if last_activity_at is None:
        return AnalysisCadence.STANDARD, STANDARD_ANALYSIS_INTERVAL

    age = scheduled_from - last_activity_at
    if age <= ACTIVE_ACTIVITY_WINDOW:
        return AnalysisCadence.ACTIVE, ACTIVE_ANALYSIS_INTERVAL
    if age <= DORMANT_ACTIVITY_WINDOW:
        return AnalysisCadence.STANDARD, STANDARD_ANALYSIS_INTERVAL
    return AnalysisCadence.DORMANT, DORMANT_ANALYSIS_INTERVAL


def _positive_jitter(repository_id: str, interval: timedelta, *, purpose: str) -> timedelta:
    maximum_seconds = int(interval.total_seconds() * _JITTER_RATIO)
    if maximum_seconds <= 0:
        return timedelta()
    digest = hashlib.sha256(f"{purpose}:{repository_id}".encode()).digest()
    offset_seconds = int.from_bytes(digest[:8], "big") % (maximum_seconds + 1)
    return timedelta(seconds=offset_seconds)


def _repository_key(repository_id: str) -> str:
    if not isinstance(repository_id, str) or not repository_id.strip():
        raise ValueError("repository_id must not be blank")
    return repository_id.strip()


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
