"""Хранение неизменяемых снимков завершённых анализов."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from backend.app.analysis.runner import AnalysisExecution


class AnalysisStore(Protocol):
    """Получение и сохранение снимков анализов по их стабильному идентификатору."""

    def get(self, analysis_id: str) -> AnalysisExecution | None:
        """Возвращает снимок анализа или None, если такого идентификатора нет."""

    def save(self, analysis_id: str, execution: AnalysisExecution) -> None:
        """Сохраняет новый неизменяемый снимок анализа."""


class InMemoryAnalysisStore:
    """Потокобезопасное временное хранилище для разработки и HTTP-тестов."""

    def __init__(self) -> None:
        self._executions: dict[str, AnalysisExecution] = {}
        self._lock = RLock()

    def get(self, analysis_id: str) -> AnalysisExecution | None:
        with self._lock:
            return self._executions.get(analysis_id.strip())

    def save(self, analysis_id: str, execution: AnalysisExecution) -> None:
        normalized_id = analysis_id.strip()
        if not normalized_id:
            raise ValueError("analysis_id must not be empty")

        with self._lock:
            if normalized_id in self._executions:
                raise ValueError("analysis_id already exists")
            self._executions[normalized_id] = execution
