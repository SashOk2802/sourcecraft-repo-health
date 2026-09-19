"""Оркестрация запуска анализаторов и сборка общего результата."""

from backend.app.analysis.runner import (
    AnalysisExecution,
    AnalyzerRegistration,
    CategoryEvaluator,
    run_analysis,
)
from backend.app.analysis.store import (
    AnalysisSnapshot,
    AnalysisStore,
    InMemoryAnalysisStore,
    PostgresAnalysisStore,
)

__all__ = [
    "AnalysisExecution",
    "AnalysisSnapshot",
    "AnalysisStore",
    "AnalyzerRegistration",
    "CategoryEvaluator",
    "InMemoryAnalysisStore",
    "PostgresAnalysisStore",
    "run_analysis",
]
