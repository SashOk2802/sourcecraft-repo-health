#!/usr/bin/env python
import os
import re

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

SUPPORTED_EXTENSIONS = {".py", ".js", ".ts", ".go", ".java", ".cpp", ".cs"}


def collect(context: AnalysisContext) -> dict:
    """Сканирует исходный код во временном репозитории на наличие TODO и FIXME."""
    # Получаем URL и ревизию строго из стабильного контракта context.repository
    repo_url = context.repository.web_url or f"https://sourcecraft.internal{context.repository.repository_slug}"
    # Используем зафиксированный в контексте коммит SHA для точности анализа ревизии
    repo = LocalGitRepository(repo_url=repo_url, branch=context.commit_sha)
    facts = {"total_files": 0, "todo_count": 0, "fixme_count": 0, "files_with_debt": 0}
    
    try:
        temp_dir = repo.clone()
        for root, _, files in os.walk(temp_dir):
            for file in files:
                if os.path.splitext(file).lower() not in SUPPORTED_EXTENSIONS:
                    continue
                    
                facts["total_files"] += 1
                full_path = os.path.join(root, file)
                
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    
                    todos = len(re.findall(r"\bTODO\b", content))
                    fixmes = len(re.findall(r"\bFIXME\b", content))
                    
                    if todos > 0 or fixmes > 0:
                        facts["todo_count"] += todos
                        facts["fixme_count"] += fixmes
                        facts["files_with_debt"] += 1
                except OSError:
                    continue
    except Exception as e:
        return {"error": str(e)}
    finally:
        repo.cleanup()
        
    return facts


def evaluate(context: AnalysisContext, raw_data: dict) -> CategoryResult:
    """Выставляет оценку за чистоту кода и плотность технического долга."""
    if "error" in raw_data:
        return CategoryResult(
            category="code_health", 
            status=DataStatus.ERROR, 
            score=None,
            summary="Не удалось выполнить анализ состояния кода из-за ошибки работы с репозиторием.",
            reason=raw_data["error"]
        )
        
    total_files = raw_data.get("total_files", 0)
    if total_files == 0:
        # По контракту StrEnum: если MEASURED невозможен, используем UNAVAILABLE (DataStatus.NO_DATA в контракте нет)
        return CategoryResult(
            category="code_health", 
            status=DataStatus.UNAVAILABLE, 
            score=None,
            summary="Анализ здоровья кода не применим: в репозитории нет поддерживаемых файлов кода.",
            reason="Отсутствуют файлы исходного кода поддерживаемых языков."
        )

    todos = raw_data.get("todo_count", 0)
    fixmes = raw_data.get("fixme_count", 0)
    
    penalty = (fixmes * 5) + (todos * 1)
    score = float(max(0, 100 - penalty))
    
    evidence = (
        Evidence(
            source="git_repository",
            reference=context.commit_sha,
            summary=f"Найдено {todos} TODO и {fixmes} FIXME в {raw_data.get('files_with_debt', 0)} файлах."
        ),
    )

    metrics = (
        MetricResult(code="total_analyzed_files", value=total_files, normalized_score=100.0, summary="Всего проанализировано файлов кода"),
        MetricResult(code="todo_count", value=todos, normalized_score=float(max(0, 100 - todos)), summary="Количество меток TODO в коде"),
        MetricResult(code="fixme_count", value=fixmes, normalized_score=float(max(0, 100 - fixmes * 5)), summary="Количество критических меток FIXME")
    )
    
    recommendations = []
    if fixmes > 0:
        recommendations.append(Recommendation(
            code="code_health_resolve_fixme",
            priority=RecommendationPriority.P1,
            problem=f"В коде присутствуют неразрешенные маркеры FIXME ({fixmes} шт.).",
            action="Устраните или закройте критические метки FIXME, перенеся их в таск-трекер.",
            rationale="Маркеры FIXME указывают на заведомо неработающий или опасный код.",
            expected_score_delta=float(min(100, fixmes * 5)),
            evidence=evidence
        ))
    if todos > 15:
        recommendations.append(Recommendation(
            code="code_health_clear_todos",
            priority=RecommendationPriority.P3,
            problem=f"В репозитории скопилось избыточное количество меток TODO ({todos} шт.).",
            action="Проведите ревизию кода и очистите его от неактуальных временных меток.",
            rationale="Слишком большое количество TODO замыливает глаз разработчикам.",
            expected_score_delta=5.0,
            evidence=evidence
        ))
        
    return CategoryResult(
        category="code_health",
        status=DataStatus.MEASURED,
        score=score,
        summary=f"Оценка чистоты кода: {score}/100. Обнаружено TODO: {todos}, FIXME: {fixmes}.",
        metrics=metrics,
        recommendations=tuple(recommendations)
    )
