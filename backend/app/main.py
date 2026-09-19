"""Точка входа HTTP для SourceCraft Repo Health."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from backend.app.analysis import (
    AnalysisSnapshot,
    AnalysisStore,
    InMemoryAnalysisStore,
    PostgresAnalysisStore,
)


def create_app(*, analysis_store: AnalysisStore | None = None) -> FastAPI:
    """Создаёт HTTP-приложение с переданным хранилищем снимков анализа."""

    store = analysis_store or _default_analysis_store()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.start()
        try:
            yield
        finally:
            await store.close()

    app = FastAPI(
        title="SourceCraft Repo Health",
        version="0.1.0",
        description="Анализ здоровья репозиториев SourceCraft.",
        lifespan=lifespan,
    )
    app.state.analysis_store = store

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Возвращает состояние процесса для локальной разработки и проверок развёртывания."""

        return {"status": "ok"}

    @app.get("/api/v1/health", tags=["system"])
    async def api_health() -> dict[str, str]:
        """Возвращает endpoint с префиксом API для proxy frontend."""

        return {"status": "ok"}

    @app.get("/api/v1/analyses/{analysis_id}", tags=["analyses"])
    async def get_analysis_status(analysis_id: str) -> dict[str, object]:
        """Возвращает состояние и ссылки на материалы сохранённого анализа."""

        normalized_id = _normalize_analysis_id(analysis_id)
        snapshot = await _require_snapshot(store, normalized_id)
        return _analysis_status_payload(snapshot, normalized_id)

    @app.get("/api/v1/analyses/{analysis_id}/report", tags=["reports"])
    async def get_report(analysis_id: str) -> dict[str, object]:
        """Возвращает JSON-отчёт для одного сохранённого снимка анализа."""

        snapshot = await _require_snapshot(store, _normalize_analysis_id(analysis_id))
        return snapshot.report

    @app.get(
        "/api/v1/analyses/{analysis_id}/report.md",
        tags=["reports"],
        response_class=PlainTextResponse,
    )
    async def get_markdown_report(analysis_id: str) -> PlainTextResponse:
        """Возвращает Markdown-отчёт по тому же снимку анализа."""

        snapshot = await _require_snapshot(store, _normalize_analysis_id(analysis_id))
        return PlainTextResponse(snapshot.markdown, media_type="text/markdown")

    return app


def _default_analysis_store() -> AnalysisStore:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return PostgresAnalysisStore(database_url)
    return InMemoryAnalysisStore()


def _normalize_analysis_id(analysis_id: str) -> str:
    normalized_id = analysis_id.strip()
    if not normalized_id:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return normalized_id


async def _require_snapshot(store: AnalysisStore, analysis_id: str) -> AnalysisSnapshot:
    snapshot = await store.get(analysis_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return snapshot


def _analysis_status_payload(
    snapshot: AnalysisSnapshot,
    analysis_id: str,
) -> dict[str, object]:
    analysis = snapshot.report["analysis"]
    return {
        "id": analysis_id,
        "status": analysis["status"],
        "repository": snapshot.report["repository"],
        "score": snapshot.report["score"],
        "isPreliminary": analysis["isPreliminary"],
        "reportUrl": f"/api/v1/analyses/{analysis_id}/report",
        "markdownReportUrl": f"/api/v1/analyses/{analysis_id}/report.md",
    }


app = create_app()
