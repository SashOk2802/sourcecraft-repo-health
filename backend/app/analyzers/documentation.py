"""Категория Documentation: полнота регламентирующих файлов репозитория.

Модуль разделён на две части. `collect` читает файлы уже склонированной
рабочей области (LocalGitRepository передаётся из слоя оркестрации — модуль
не строит git URL и не хранит токены) и возвращает факты. `evaluate` — чистая
функция: на одних и тех же фактах всегда даёт один и тот же результат.
"""

from __future__ import annotations

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
from backend.app.integrations.git_repository import GitCloneError, LocalGitRepository

README_PATHS = ("README.md", "README.rst")
CONTRIBUTING_PATHS = ("CONTRIBUTING.md",)
CODEOWNERS_PATHS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
LICENSE_PATHS = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "LICENCE")

# Штрафы за отсутствие регламентов (источник правды для «Как считаем» —
# docs/scoring-methodology.md §4.4; здесь значения должны совпадать).
PENALTY_README = 35.0
PENALTY_CONTRIBUTING = 20.0
PENALTY_LICENSE = 15.0
PENALTY_CODEOWNERS = 15.0
PENALTY_INSTRUCTIONS = 15.0

# Заголовки, посвящённые запуску/тестированию проекта: настоящий сигнал раздела,
# а не подстрока в тексте ("test" внутри "latest", "run" внутри "runtime").
_SECTION_HEADING = re.compile(
    r"^\s{0,3}#{1,6}\s+.+\b("
    r"how\s+to\s+(run|test|start)"
    r"|getting\s+started"
    r"|quick\s+start"
    r"|running\b"
    r"|run\s+the\b"
    r"|tests?\b"
    r"|testing\b"
    r"|запуск\b"
    r"|запустить\b"
    r"|запускается\b"
    r"|как\s+запустить\b"
    r"|тесты\b"
    r"|тестирование\b"
    r"|установка\s+и\s+запуск\b"
    r")\b",
    re.IGNORECASE | re.MULTILINE,
)

_FENCED_CODE_BLOCK = re.compile(r"```[\w+-]*\n(.*?)```", re.DOTALL)

# Реальная команда запуска/тестов внутри fenced code block.
_COMMAND_IN_BLOCK = re.compile(
    r"\b("
    r"docker\s+(compose\s+)?(up|run)"
    r"|docker-compose\s+(up|run)"
    r"|pytest"
    r"|python\s+-m\s+pytest"
    r"|python\s+(manage\.py|app\.py)"
    r"|npm\s+(run\s+)?(test|start|dev)"
    r"|yarn\s+(test|start|dev)"
    r"|pnpm\s+(run\s+)?(test|start|dev)"
    r"|make\s+(test|run|start)"
    r"|go\s+(test|run)"
    r"|cargo\s+(test|run)"
    r"|mvn\s+test"
    r"|gradle\s+test"
    r"|dotnet\s+test"
    r"|bundle\s+exec\s+rspec"
    r")\b",
    re.IGNORECASE,
)


def collect(repo: LocalGitRepository) -> dict:
    """Собирает факты о наличии регламентирующих файлов документации.

    Требует уже склонированную рабочую область: сам URL репозитория и токен
    сюда не попадают (см. providers.repo_content_analyzer_provider).
    """
    temp_dir = repo.temp_dir
    if temp_dir is None:
        raise GitCloneError("Рабочая область git-репозитория не подготовлена.")

    facts: dict = {
        "has_readme": _any_exists(repo, README_PATHS),
        "has_contributing": _any_exists(repo, CONTRIBUTING_PATHS),
        "has_codeowners": _any_exists(repo, CODEOWNERS_PATHS),
        "has_license": _any_exists(repo, LICENSE_PATHS),
    }

    readme_content = ""
    for name in README_PATHS:
        readme_content = repo.read_file(name) or ""
        if readme_content:
            break
    facts["has_shortcuts"] = _has_run_instructions(readme_content)
    return facts


def _any_exists(repo: LocalGitRepository, paths: tuple[str, ...]) -> bool:
    return any(repo.file_exists(name) for name in paths)


def _has_run_instructions(readme_content: str) -> bool:
    """Определяет наличие реального раздела «как запустить/протестировать».

    Сигналом считается тематический заголовок или fenced code block с командой
    запуска/тестов. Голое слово «latest» или «runtime» в тексте сигналом
    не является.
    """
    if not readme_content.strip():
        return False
    if _SECTION_HEADING.search(readme_content):
        return True
    return any(
        _COMMAND_IN_BLOCK.search(block) for block in _FENCED_CODE_BLOCK.findall(readme_content)
    )


def evaluate(context: AnalysisContext, raw_data: dict) -> CategoryResult:
    """Рассчитывает оценку за полноту документации."""
    if "error" in raw_data:
        return CategoryResult(
            category="documentation",
            status=DataStatus.ERROR,
            score=None,
            summary="Не удалось выполнить анализ документации из-за системной ошибки.",
            reason=raw_data["error"],
        )

    metrics: list[MetricResult] = []
    recommendations: list[Recommendation] = []
    score = 100.0

    checks = [
        (
            "has_readme",
            "README.md",
            PENALTY_README,
            "Добавьте README.md с описанием архитектуры и назначения проекта.",
            RecommendationPriority.P1,
        ),
        (
            "has_contributing",
            "CONTRIBUTING.md",
            PENALTY_CONTRIBUTING,
            "Создайте CONTRIBUTING.md для фиксации правил ведения веток и код-ревью.",
            RecommendationPriority.P2,
        ),
        (
            "has_license",
            "LICENSE",
            PENALTY_LICENSE,
            "Добавьте файл LICENSE или COPYING для соблюдения юридической чистоты.",
            RecommendationPriority.P2,
        ),
        (
            "has_codeowners",
            "CODEOWNERS",
            PENALTY_CODEOWNERS,
            "Настройте файл CODEOWNERS для автоматического назначения ревьюеров в PR.",
            RecommendationPriority.P3,
        ),
        (
            "has_shortcuts",
            "Инструкции запуска",
            PENALTY_INSTRUCTIONS,
            "Добавьте в README.md разделы с командами быстрого запуска проекта и тестов.",
            RecommendationPriority.P2,
        ),
    ]

    for key, label, penalty, rec_msg, priority in checks:
        # Одна причина — отсутствующий README: отдельный штраф и рекомендация
        # за «нет инструкций запуска» при отсутствующем README не выдаются.
        if key == "has_shortcuts" and not raw_data.get("has_readme", False):
            continue

        has_file = raw_data.get(key, False)

        metrics.append(
            MetricResult(
                code=key,
                value=1 if has_file else 0,
                normalized_score=100.0 if has_file else 0.0,
                summary=f"Наличие файла/информации: {label}",
            )
        )

        if not has_file:
            # Evidence называет конкретный недостающий файл. Для инструкций
            # запуска это README.md: раздел должен находиться там.
            if key == "has_shortcuts":
                evidence = (
                    Evidence(
                        source="repository_structure",
                        reference="README.md",
                        summary="Инструкции запуска и тестов отсутствуют в README.md.",
                    ),
                )
            else:
                evidence = (
                    Evidence(
                        source="repository_structure",
                        reference=label,
                        summary=f"{label} отсутствует в корне репозитория.",
                    ),
                )
            score -= penalty
            recommendations.append(
                Recommendation(
                    code=f"doc_missing_{key}",
                    priority=priority,
                    problem=f"В репозитории проекта отсутствует или не заполнен {label}.",
                    action=rec_msg,
                    rationale="Качественная техническая документация и прозрачные правила снижают TTM и упрощают онбординг.",
                    expected_score_delta=penalty,
                    evidence=evidence,
                )
            )

    return CategoryResult(
        category="documentation",
        status=DataStatus.MEASURED,
        score=float(max(0, score)),
        summary=f"Оценка документации: {max(0, score):.0f}/100. Проверены базовые файлы репозитория.",
        metrics=tuple(metrics),
        recommendations=tuple(recommendations),
    )
