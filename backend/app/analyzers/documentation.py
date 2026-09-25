"""Воспроизводимая оценка базовой документации подготовленного Git-дерева."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    Evidence,
    MetricResult,
    Recommendation,
    RecommendationPriority,
)
from backend.app.integrations.git_repository import LocalGitRepository

CATEGORY_CODE = "documentation"


@dataclass(frozen=True, slots=True)
class DocumentationFacts:
    has_readme: bool
    has_contributing: bool
    has_codeowners: bool
    has_license: bool
    has_shortcuts: bool


def collect(repository: LocalGitRepository) -> DocumentationFacts:
    """Собирает булевы признаки; текст README не попадает в результат."""

    readme = repository.read_file("README.md") or ""
    return DocumentationFacts(
        has_readme=repository.file_exists("README.md"),
        has_contributing=repository.file_exists("CONTRIBUTING.md"),
        has_codeowners=any(
            repository.file_exists(path) for path in ("CODEOWNERS", ".github/CODEOWNERS")
        ),
        has_license=any(
            repository.file_exists(path) for path in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")
        ),
        has_shortcuts=any(word in readme.lower() for word in ("docker", "pytest", "npm test", "make test")),
    )


def evaluate(context: AnalysisContext, facts: DocumentationFacts) -> CategoryResult:
    """Считает категорию без Git, сети или системного времени."""

    evidence = (Evidence("repository_structure", context.commit_sha, "Проверены файлы в Git-дереве."),)
    checks = (
        ("has_readme", "README.md", 35.0, RecommendationPriority.P1),
        ("has_contributing", "CONTRIBUTING.md", 20.0, RecommendationPriority.P2),
        ("has_license", "LICENSE", 15.0, RecommendationPriority.P2),
        ("has_codeowners", "CODEOWNERS", 15.0, RecommendationPriority.P3),
        ("has_shortcuts", "инструкции запуска", 15.0, RecommendationPriority.P2),
    )
    score = 100.0
    metrics: list[MetricResult] = []
    recommendations: list[Recommendation] = []
    for key, label, penalty, priority in checks:
        present = getattr(facts, key)
        metrics.append(MetricResult(key, int(present), 100.0 if present else 0.0, f"Наличие: {label}"))
        if not present:
            score -= penalty
            recommendations.append(
                Recommendation(
                    code=f"documentation_missing_{key}",
                    priority=priority,
                    problem=f"В репозитории отсутствует {label}.",
                    action=f"Добавьте или заполните {label}.",
                    rationale="Документация снижает риски передачи и сопровождения проекта.",
                    expected_score_delta=penalty,
                    evidence=evidence,
                )
            )
    score = max(score, 0.0)
    return CategoryResult(
        category=CATEGORY_CODE,
        status=DataStatus.MEASURED,
        score=score,
        summary=f"Оценка документации: {score:.0f}/100.",
        metrics=tuple(metrics),
        recommendations=tuple(recommendations),
    )


def make_analyzer(facts_provider: Callable[[], DocumentationFacts]) -> Callable[[AnalysisContext], CategoryResult]:
    """Связывает подготовленное рабочее дерево с чистой функцией оценки."""

    return lambda context: evaluate(context, facts_provider())
