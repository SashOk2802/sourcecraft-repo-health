"""Точка входа HTTP для SourceCraft Repo Health."""

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from backend.app.analysis import AnalysisExecution, AnalysisStore, InMemoryAnalysisStore
from backend.app.reporting import build_report_payload, render_markdown_report


def create_app(*, analysis_store: AnalysisStore | None = None) -> FastAPI:
    """Создаёт HTTP-приложение с переданным хранилищем снимков анализа."""

    store = analysis_store or InMemoryAnalysisStore()
    app = FastAPI(
        title="SourceCraft Repo Health",
        version="0.1.0",
        description="Анализ здоровья репозиториев SourceCraft.",
    )
    app.state.analysis_store = store

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Возвращает состояние процесса для локальной разработки и проверок развёртывания."""

        return {"status": "ok"}

    @app.get("/api/v1/health", tags=["system"])
    def api_health() -> dict[str, str]:
        """Возвращает endpoint с префиксом API для proxy frontend."""

        return {"status": "ok"}

    @app.get("/api/v1/analyses/{analysis_id}/report", tags=["reports"])
    def get_report(analysis_id: str) -> dict[str, object]:
        """Возвращает JSON-отчёт для одного сохранённого снимка анализа."""

        normalized_id = _normalize_analysis_id(analysis_id)
        execution = _require_execution(store, normalized_id)
        return build_report_payload(execution, analysis_id=normalized_id)

    @app.get(
        "/api/v1/analyses/{analysis_id}/report.md",
        tags=["reports"],
        response_class=PlainTextResponse,
    )
    def get_markdown_report(analysis_id: str) -> PlainTextResponse:
        """Возвращает Markdown-отчёт по тому же снимку анализа."""

        normalized_id = _normalize_analysis_id(analysis_id)
        execution = _require_execution(store, normalized_id)
        return PlainTextResponse(
            render_markdown_report(execution, analysis_id=normalized_id),
            media_type="text/markdown",
        )

    return app

def _normalize_analysis_id(analysis_id: str) -> str:
    normalized_id = analysis_id.strip()
    if not normalized_id:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return normalized_id

def _require_execution(store: AnalysisStore, analysis_id: str) -> AnalysisExecution:
    execution = store.get(analysis_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return execution


app = create_app()
