from app.contracts import AnalysisContext, CategoryResult, DataStatus, MetricItem, Recommendation
from app.integrations.git_repository import LocalGitRepository

def collect(context: AnalysisContext) -> dict:
    """Собирает факты о наличии документации."""
    repo = LocalGitRepository(repo_url=context.repository_url, branch=context.branch)
    facts = {}
    try:
        repo.clone()
        facts["has_readme"] = repo.file_exists("README.md")
        facts["has_contributing"] = repo.file_exists("CONTRIBUTING.md")
        facts["has_codeowners"] = repo.file_exists("CODEOWNERS")
        
        # Проверяем, есть ли в README упоминания про запуск/тесты
        readme_content = repo.read_file("README.md") or ""
        facts["has_shortcuts"] = any(x in readme_content.lower() for x in ["docker", "run", "pytest", "test"])
    except Exception as e:
        facts["error"] = str(e)
    finally:
        repo.cleanup()
        
    return facts

def evaluate(context: AnalysisContext, raw_data: dict) -> CategoryResult:
    """Рассчитывает оценку документации от 0 до 100."""
    if "error" in raw_data:
        return CategoryResult(
            category="documentation",
            status=DataStatus.ERROR,
            reason=raw_data["error"]
        )

    metrics = []
    recommendations = []
    score = 100

    # Правила начисления штрафов за отсутствие документации
    checks = [
        ("has_readme", "README.md", 40, "Добавьте README.md с описанием архитектуры и назначения проекта.", 1),
        ("has_contributing", "CONTRIBUTING.md", 30, "Создайте CONTRIBUTING.md для фиксации правил ведения веток и код-ревью.", 2),
        ("has_codeowners", "CODEOWNERS", 15, "Настройте файл CODEOWNERS для автоматического назначения ревьюеров в PR.", 3),
        ("has_shortcuts", "Инструкции", 15, "Добавьте в README.md разделы с командами быстрого запуска проекта и тестов.", 2)
    ]

    for key, label, penalty, rec_msg, priority in checks:
        has_file = raw_data.get(key, False)
        metrics.append(MetricItem(name=key, value=has_file))
        if not has_file:
            score -= penalty
            recommendations.append(Recommendation(
                id=f"doc_missing_{key}",
                message=rec_msg,
                priority=priority
            ))

    return CategoryResult(
        category="documentation",
        status=DataStatus.MEASURED,
        score=max(0, score),
        metrics=metrics,
        recommendations=recommendations
    )
