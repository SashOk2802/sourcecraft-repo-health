#!/usr/bin/env python
from app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    Evidence,
    MetricResult,
    Recommendation,
    RecommendationPriority,
)
from app.integrations.git_repository import LocalGitRepository


def collect(context: AnalysisContext) -> dict:
    """Собирает факты о наличии документации, инструкций запуска и лицензии."""
    repo_url = context.repository.web_url or f"https://sourcecraft.internal{context.repository.repository_slug}"
    repo = LocalGitRepository(repo_url=repo_url, branch="main")
    facts = {}
    try:
        repo.clone()
        facts["has_readme"] = repo.file_exists("README.md")
        facts["has_contributing"] = repo.file_exists("CONTRIBUTING.md")
        facts["has_codeowners"] = repo.file_exists("CODEOWNERS")
        
        # Проверка лицензии: ищем популярные форматы названий файлов
        facts["has_license"] = any(
            repo.file_exists(name) for name in ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"]
        )
        
        # Проверяем, есть ли в README упоминания про запуск/тесты
        readme_content = repo.read_file("README.md") or ""
        facts["has_shortcuts"] = any(x in readme_content.lower() for x in ["docker", "run", "pytest", "test", "python"])
    except Exception as e:
        facts["error"] = str(e)
    finally:
        repo.cleanup()
        
    return facts


def evaluate(context: AnalysisContext, raw_data: dict) -> CategoryResult:
    """Рассчитывает оценку документации на основе наличия ключевых файлов."""
    if "error" in raw_data:
        return CategoryResult(
            category="documentation",
            status=DataStatus.ERROR,
            score=None,
            summary="Не удалось выполнить анализ документации из-за ошибки работы с репозиторием.",
            reason=raw_data["error"]
        )

    evidence = (
        Evidence(
            source="repository_structure",
            reference=context.commit_sha,
            summary="Анализ файлов в корневом каталоге проекта."
        ),
    )

    metrics = []
    recommendations = []
    score = 100.0

    # Правила начисления штрафов, обновленные с учетом ЛИЦЕНЗИИ (баланс весов подогнан под 100 баллов)
    # README (35), CONTRIBUTING (20), LICENSE (15), CODEOWNERS (15), Инструкции (15)
    checks = [
        ("has_readme", "README.md", 35.0, "Добавьте README.md с описанием архитектуры и назначения проекта.", RecommendationPriority.P1),
        ("has_contributing", "CONTRIBUTING.md", 20.0, "Создайте CONTRIBUTING.md для фиксации правил ведения веток и код-ревью.", RecommendationPriority.P2),
        ("has_license", "LICENSE", 15.0, "Добавьте файл LICENSE или COPYING для юридической чистоты открытого кода.", RecommendationPriority.P2),
        ("has_codeowners", "CODEOWNERS", 15.0, "Настройте файл CODEOWNERS для автоматического назначения ревьюеров в PR.", RecommendationPriority.P3),
        ("has_shortcuts", "Инструкции запуска", 15.0, "Добавьте в README.md разделы с командами быстрого запуска проекта и тестов.", RecommendationPriority.P2)
    ]

    for key, label, penalty, rec_msg, priority in checks:
        has_file = raw_data.get(key, False)
        
        metrics.append(MetricResult(
            code=key,
            value=1 if has_file else 0,
            normalized_score=100.0 if has_file else 0.0,
            summary=f"Наличие файла/информации: {label}"
        ))
        
        if not has_file:
            score -= penalty
            recommendations.append(Recommendation(
                code=f"doc_missing_{key}",
                priority=priority,
                problem=f"В репозитории отсутствует или не заполнен {label}.",
                action=rec_msg,
                rationale="Качественная техническая документация снижает порог входа для новых разработчиков и стандартизирует процессы.",
                expected_score_delta=penalty,
                evidence=evidence
            ))

    return CategoryResult(
        category="documentation",
        status=DataStatus.MEASURED,
        score=float(max(0, score)),
        summary=f"Оценка документации: {max(0, score)}/100. Проверены базовые регламентирующие файлы проекта.",
        metrics=tuple(metrics),
        recommendations=tuple(recommendations)
    )
