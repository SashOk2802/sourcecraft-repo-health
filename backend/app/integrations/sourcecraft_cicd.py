"""Получение и проверка данных CI/CD из SourceCraft."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.app.contracts import RepositoryRef
from backend.app.integrations.sourcecraft import (
    SourceCraftClient,
    SourceCraftRequestError,
    SourceCraftResponseError,
)

_SOURCECRAFT_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RUN_STATUSES = frozenset(
    {
        "created",
        "prepared",
        "processing",
        "success",
        "failed",
        "canceled",
        "timeout",
        "skipped",
        "awaiting_approval",
        "rejected",
    }
)
# Категории запуска из https://sourcecraft.dev/portal/docs/en/api-ref/CICD/ListRuns.
# repository_event — категория, а не конкретное имя события EventBus.
_EVENT_TYPES = frozenset({"push", "pr_update", "manual", "restart", "schedule", "repository_event"})


@dataclass(frozen=True, slots=True)
class SourceCraftCiRun:
    """Минимальные поля запуска CI, нужные будущему анализатору категории.

    SourceCraft может вернуть пустой ``id`` запуска. Поэтому это поле не
    используется как идентификатор: для сопоставления запусков нужен ``slug``.
    """

    id: str
    slug: str
    status: str
    event_type: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    workflow_slugs: tuple[str, ...]


class SourceCraftCicdClient:
    """Читает CI-запуски репозитория через безопасный общий HTTP-клиент."""

    def __init__(self, sourcecraft_client: SourceCraftClient) -> None:
        self._sourcecraft_client = sourcecraft_client

    def list_runs(
        self,
        repository: RepositoryRef,
        *,
        page_size: int = 100,
    ) -> tuple[SourceCraftCiRun, ...]:
        """Возвращает все доступные CI-запуски репозитория из всех страниц."""

        path = _runs_path(repository)
        payloads = self._sourcecraft_client.get_paginated_objects(
            path,
            items_field="runs",
            page_size=page_size,
        )
        return tuple(_parse_run(payload) for payload in payloads)


def _runs_path(repository: RepositoryRef) -> str:
    _validate_slug(repository.organization_slug, "organization_slug")
    _validate_slug(repository.repository_slug, "repository_slug")
    return f"/repos/{repository.organization_slug}/{repository.repository_slug}/cicd/runs"


def _validate_slug(value: str, field: str) -> None:
    if not _SOURCECRAFT_SLUG.fullmatch(value):
        raise SourceCraftRequestError(f"SourceCraft {field} must be a single URL path segment")


def _parse_run(payload: dict[str, Any]) -> SourceCraftCiRun:
    status = _require_string(payload, "status")
    if status not in _RUN_STATUSES:
        raise SourceCraftResponseError("SourceCraft returned an unknown CI run status")

    event_type = _require_string(payload, "event_type")
    if event_type not in _EVENT_TYPES:
        raise SourceCraftResponseError("SourceCraft returned an unknown CI event type")

    dates = payload.get("dates")
    if not isinstance(dates, dict):
        raise SourceCraftResponseError("SourceCraft CI run must contain dates")

    workflows = payload.get("workflows")
    if not isinstance(workflows, list) or not all(isinstance(item, dict) for item in workflows):
        raise SourceCraftResponseError("SourceCraft CI run must contain an array of workflows")

    started_at = _parse_timestamp(dates, "started_at", optional=True)
    finished_at = _parse_timestamp(dates, "finished_at", optional=True)
    if (
        started_at is not None
        and finished_at is not None
        and finished_at < started_at
    ):
        raise SourceCraftResponseError(
            "SourceCraft CI run finished_at must not precede started_at"
        )

    return SourceCraftCiRun(
        # В наблюдённом ответе платформы id бывает пустым, однако slug заполнен.
        id=_require_string(payload, "id", allow_empty=True),
        slug=_require_string(payload, "slug"),
        status=status,
        event_type=event_type,
        created_at=_parse_timestamp(dates, "created_at"),
        started_at=started_at,
        finished_at=finished_at,
        updated_at=_parse_timestamp(dates, "updated_at"),
        workflow_slugs=tuple(_require_string(workflow, "slug") for workflow in workflows),
    )


def _require_string(
    payload: dict[str, Any],
    field: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise SourceCraftResponseError(f"SourceCraft CI run must contain a string {field}")
    return value


def _parse_timestamp(
    payload: dict[str, Any],
    field: str,
    *,
    optional: bool = False,
) -> datetime | None:
    value = payload.get(field)
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise SourceCraftResponseError(f"SourceCraft CI run must contain an ISO timestamp {field}")

    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise SourceCraftResponseError(
            f"SourceCraft CI run contains an invalid timestamp {field}"
        ) from error

    if timestamp.tzinfo is None:
        raise SourceCraftResponseError(
            f"SourceCraft CI run timestamp {field} must include a timezone"
        )
    return timestamp.astimezone(UTC)
