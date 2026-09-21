import os
import re

from app.contracts import AnalysisContext, CategoryResult, DataStatus, MetricItem, Recommendation
from app.integrations.git_repository import LocalGitRepository

# Расширения файлов для анализа
SUPPORTED_EXTENSIONS = {".py", ".js", ".ts", ".go", ".java", ".cpp", ".cs"}


def collect(context: AnalysisContext) -> dict:
    """Сканирует исходный код на наличие TODO и FIXME."""
    repo = LocalGitRepository(repo_url=context.repository_url, branch=context.branch)
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
                    # Заменили общий Exception на конкретный OSError по требованию линтера
                    continue
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}

    finally:
        repo.cleanup()
        
    return facts


def evaluate(context: AnalysisContext, raw_data: dict) -> CategoryResult:
    """Выставляет оценку за чистоту кода."""
    if "error" in raw_data:
        return CategoryResult(category="code_health", status=DataStatus.ERROR, reason=raw_data["error"])
        
    total_files = raw_data.get("total_files", 0)
    if total_files == 0:
        return CategoryResult(
            category="code_health", 
            status=DataStatus.NO_DATA, 
            reason="В репозитории нет поддерживаемых файлов кода для анализа."
        )

    todos = raw_data.get("todo_count", 0)
    fixmes = raw_data.get("fixme_count", 0)
    
    penalty = (fixmes * 5) + (todos * 1)
    score = max(0, 100 - penalty)
    
    metrics = [
        MetricItem(name="total_analyzed_files", value=total_files),
        MetricItem(name="todo_count", value=todos),
        MetricItem(name="fixme_count", value=fixmes)
    ]
    
    recommendations = []
    if fixmes > 0:
        recommendations.append(Recommendation(
            id="code_health_resolve_fixme",
            message="Устраните критические метки FIXME в коде.",
            priority=1
        ))
    if todos > 15:
        recommendations.append(Recommendation(
            id="code_health_clear_todos",
            message="Очистите код от скопившихся меток TODO.",
            priority=3
        ))
        
    return CategoryResult(
        category="code_health",
        status=DataStatus.MEASURED,
        score=score,
        metrics=metrics,
        recommendations=recommendations
    )
