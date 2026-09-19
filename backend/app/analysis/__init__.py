"""Оркестрация запуска анализаторов и сборка общего результата."""

from backend.app.analysis.executor import AnalysisExecutionService
from backend.app.analysis.jobs import (
    AnalysisJob,
    AnalysisJobNotFoundError,
    AnalysisJobStatus,
    AnalysisJobStore,
    AnalysisJobTransitionError,
    InMemoryAnalysisJobStore,
    PostgresAnalysisJobStore,
)
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
    normalize_analysis_id,
)

__all__ = [
    "AnalysisExecution",
    "AnalysisExecutionService",
    "AnalysisJob",
    "AnalysisJobNotFoundError",
    "AnalysisJobStatus",
    "AnalysisJobStore",
    "AnalysisJobTransitionError",
    "AnalysisSnapshot",
    "AnalysisStore",
    "AnalyzerRegistration",
    "CategoryEvaluator",
    "InMemoryAnalysisJobStore",
    "InMemoryAnalysisStore",
    "PostgresAnalysisJobStore",
    "PostgresAnalysisStore",
    "normalize_analysis_id",
    "run_analysis",
]
