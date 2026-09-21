"""Нормализация данных AppSec из SourceCraft для категории безопасности."""

from __future__ import annotations

from typing import Any

from backend.app.contracts import DataStatus


def appsec_payload_status(payload: dict[str, Any] | list[Any] | None) -> DataStatus:
    """Преобразует ответ SourceCraft в честный статус доступности AppSec.

    SourceCraft вернул ``null`` для репозитория без доступных AppSec-результатов.
    Это не равно пустому списку findings: ``null`` означает, что источник
    недоступен, а список (в том числе пустой) — что ответ был получен.
    """

    if payload is None:
        return DataStatus.UNAVAILABLE
    return DataStatus.MEASURED
