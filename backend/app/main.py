"""Точка входа HTTP для SourceCraft Repo Health."""

from fastapi import FastAPI

app = FastAPI(
    title="SourceCraft Repo Health",
    version="0.1.0",
    description="Анализ здоровья репозиториев SourceCraft.",
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Возвращает состояние процесса для локальной разработки и проверок развёртывания."""

    return {"status": "ok"}


@app.get("/api/v1/health", tags=["system"])
def api_health() -> dict[str, str]:
    """Возвращает endpoint с префиксом API для proxy frontend."""

    return {"status": "ok"}
