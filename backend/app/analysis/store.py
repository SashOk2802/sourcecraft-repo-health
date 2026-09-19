"""Хранение неизменяемых снимков завершённых анализов."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
from threading import RLock
from typing import Protocol

import asyncpg

from backend.app.analysis.runner import AnalysisExecution
from backend.app.reporting import build_report_payload, render_markdown_report


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    """Готовое неизменяемое представление анализа для выдачи через API."""

    report: dict[str, object]
    markdown: str


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


class PostgresAnalysisStore:
    """PostgreSQL-хранилище неизменяемых снимков анализов."""

    def __init__(self, database_url: str) -> None:
        self._database_url = _normalize_database_url(database_url)
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        """Открывает пул подключений и создаёт таблицу MVP при первом запуске."""

        if self._pool is not None:
            return

        self._pool = await asyncpg.create_pool(self._database_url)
        await self._pool.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_snapshots (
                analysis_id TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                report JSONB NOT NULL,
                markdown TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

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

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Analysis store is not started.")
        return self._pool


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
