"""Оркестрация запуска анализаторов и сборка общего результата."""

from backend.app.analysis.dispatch import (
    AnalysisDispatcher,
    AnalysisPrincipal,
    AnalyzerProvider,
    InProcessAnalysisDispatcher,
    RepositoryContextResolver,
)
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
    "AnalysisDispatcher",
    "AnalysisExecution",
    "AnalysisExecutionService",
    "AnalysisJob",
    "AnalysisPrincipal",
    "AnalysisJobNotFoundError",
    "AnalysisJobStatus",
    "AnalysisJobStore",
    "AnalysisJobTransitionError",
    "AnalysisSnapshot",
    "AnalysisStore",
    "AnalyzerProvider",
    "AnalyzerRegistration",
    "CategoryEvaluator",
    "InMemoryAnalysisJobStore",
    "InMemoryAnalysisStore",
    "InProcessAnalysisDispatcher",
    "PostgresAnalysisJobStore",
    "PostgresAnalysisStore",
    "RepositoryContextResolver",
    "normalize_analysis_id",
    "run_analysis",
]
