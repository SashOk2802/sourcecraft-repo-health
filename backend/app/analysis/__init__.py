"""Оркестрация запуска анализаторов и сборка общего результата."""

from backend.app.analysis.runner import (
    AnalysisExecution,
    AnalyzerRegistration,
    CategoryEvaluator,
    run_analysis,
)

__all__ = [
    "AnalysisExecution",
    "AnalyzerRegistration",
    "CategoryEvaluator",
    "run_analysis",
]
