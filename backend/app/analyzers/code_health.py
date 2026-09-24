"""Категория Code health: плотность технического долга в коде репозитория.

Модуль разделён на две части. `collect` сканирует уже склонированную рабочую
область (LocalGitRepository передаётся из слоя оркестрации — сам модуль не
строит URL и не хранит токены) и возвращает факты. `evaluate` — чистая функция:
на одних и тех же фактах всегда даёт один и тот же результат.
"""

from __future__ import annotations

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
from backend.app.integrations.git_repository import GitCloneError, LocalGitRepository

SUPPORTED_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".java",
    ".cpp",
    ".cs",
    ".rs",
}

# Директории, содержимое которых не является исходным кодом проекта.
EXCLUDED_DIRECTORIES = {
    ".git",
    "node_modules",
    "vendor",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}

# Плотность долга: штрафные баллы за маркер, нормированные на число файлов.
# Величины задокументированы в docs/scoring-methodology.md §5 (источник правды
# для экрана «Как считаем» — этот документ, а не только код).
FIXME_PENALTY_PER_MARKER = 5.0
TODO_PENALTY_PER_MARKER = 1.0
DENSITY_SCALE_TO_POINTS = 100.0

# Максимум байт, читаемых из одного файла репозитория: файлы больше лимита
# обрезаются, а не читаются целиком (безопасное чтение через read_file_safe).
MAX_FILE_READ_BYTES = 1_048_576

# Порог, после которого рекомендация по TODO считается обоснованной.
TODO_RECOMMENDATION_THRESHOLD = 15

# Одинокий FIXME — ещё не подтверждённый критический дефект; жёсткая формулировка
# и P1 допустимы от этого числа маркеров.
FIXME_CRITICAL_COUNT = 2

MAX_EVIDENCE_ENTRIES = 20

TODO_PATTERN = re.compile(r"\btodo\b", re.IGNORECASE)
FIXME_PATTERN = re.compile(r"\bfixme\b", re.IGNORECASE)


def collect(repo: LocalGitRepository) -> dict:
    """Сканирует клон на наличие TODO и FIXME и возвращает факты.

    Репозиторий уже аутентифицирован и склонирован вызывающей стороной
    (провайдер анализа); здесь выполняется только чтение файлов.
    """
    temp_dir = repo.temp_dir
    if temp_dir is None:
        raise GitCloneError("Рабочая область git-репозитория не подготовлена.")

    facts: dict = {
        "total_files": 0,
        "todo_count": 0,
        "fixme_count": 0,
        "files_with_debt": 0,
        "occurrences": [],
    }

    for root, dirs, files in os.walk(temp_dir):
        dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRECTORIES]
        for file in files:
            if os.path.splitext(file)[1].lower() not in SUPPORTED_EXTENSIONS:
                continue

            facts["total_files"] += 1
            relative_path = os.path.relpath(os.path.join(root, file), temp_dir).replace(
                os.sep, "/"
            )
            content = repo.read_file_safe(relative_path, max_bytes=MAX_FILE_READ_BYTES)
            if content is None:
                # Путь вне temp_dir (симлинк наружу и т.п.) или ошибка чтения —
                # файл пропускается, сканирование продолжается без прерывания.
                continue

            todos = len(TODO_PATTERN.findall(content))
            fixmes = len(FIXME_PATTERN.findall(content))
            if todos == 0 and fixmes == 0:
                continue

            facts["todo_count"] += todos
            facts["fixme_count"] += fixmes
            facts["files_with_debt"] += 1
            facts["occurrences"].extend(
                _marker_occurrences(relative_path, content, TODO_PATTERN, "TODO")
            )
            facts["occurrences"].extend(
                _marker_occurrences(relative_path, content, FIXME_PATTERN, "FIXME")
            )

    return facts


def _marker_occurrences(
    relative_path: str,
    content: str,
    pattern: re.Pattern[str],
    kind: str,
) -> list[dict]:
    """Возвращает путь и номер строки каждого маркера (для evidence)."""
    occurrences: list[dict] = []
    for match in pattern.finditer(content):
        line = content.count("\n", 0, match.start()) + 1
        occurrences.append({"kind": kind, "path": relative_path, "line": line})
    return occurrences


def evaluate(context: AnalysisContext, raw_data: dict) -> CategoryResult:
    """Выставляет оценку за чистоту кода и плотность технического долга."""
    if "error" in raw_data:
        return CategoryResult(
            category="code_health",
            status=DataStatus.ERROR,
            score=None,
            summary="Не удалось выполнить анализ состояния кода из-за ошибки работы с репозиторием.",
            reason=raw_data["error"],
        )

    total_files = raw_data.get("total_files", 0)
    if total_files == 0:
        # Нет поддерживаемых файлов — категория к репозиторию не применима,
        # а не «данные недоступны»: not_applicable исключается и из Score,
        # и из знаменателя Coverage (методика v1, §1).
        return CategoryResult(
            category="code_health",
            status=DataStatus.NOT_APPLICABLE,
            score=None,
            summary="Анализ здоровья кода не применим: в репозитории нет поддерживаемых файлов кода.",
            reason="Отсутствуют файлы исходного кода поддерживаемых языков.",
        )

    todos = raw_data.get("todo_count", 0)
    fixmes = raw_data.get("fixme_count", 0)

    score = float(_score_for(fixmes, todos, total_files))

    occurrence_evidence = _build_occurrence_evidence(raw_data.get("occurrences", []))
    category_evidence = (
        Evidence(
            source="git_repository",
            reference=context.commit_sha,
            summary=f"Найдено {todos} TODO и {fixmes} FIXME в "
            f"{raw_data.get('files_with_debt', 0)} файлах.",
        ),
    )

    metrics = (
        MetricResult(
            code="total_analyzed_files",
            value=total_files,
            normalized_score=None,
            summary="Всего проанализировано файлов кода",
        ),
        MetricResult(
            code="todo_count",
            value=todos,
            normalized_score=None,
            summary="Количество меток TODO в коде",
        ),
        MetricResult(
            code="fixme_count",
            value=fixmes,
            normalized_score=None,
            summary="Количество критических меток FIXME",
        ),
    )

    recommendations = []
    if fixmes > 0:
        critical = fixmes >= FIXME_CRITICAL_COUNT
        priority = RecommendationPriority.P1 if critical else RecommendationPriority.P2
        rationale = (
            "Маркеры FIXME указывают на заведомо неработающий или опасный код."
            if critical
            else "Единичный FIXME стоит перенести в трекер, чтобы он не потерялся."
        )
        delta_before = score
        delta_after = _score_for(0, todos, total_files)
        recommendations.append(
            Recommendation(
                code="code_health_resolve_fixme",
                priority=priority,
                problem=f"В коде присутствуют неразрешенные маркеры FIXME ({fixmes} шт.).",
                action="Устраните или закройте критические метки FIXME, перенеся их в таск-трекер.",
                rationale=rationale,
                expected_score_delta=float(max(0.0, delta_after - delta_before)),
                evidence=occurrence_evidence or category_evidence,
            )
        )
    if todos > TODO_RECOMMENDATION_THRESHOLD:
        delta_before = score
        delta_after = _score_for(fixmes, 0, total_files)
        recommendations.append(
            Recommendation(
                code="code_health_clear_todos",
                priority=RecommendationPriority.P3,
                problem=f"В репозитории скопилось избыточное количество меток TODO ({todos} шт.).",
                action="Проведите ревизию кода и очистите его от неактуальных временных меток.",
                rationale="Слишком большое количество TODO замыливает глаз разработчикам.",
                expected_score_delta=float(max(0.0, delta_after - delta_before)),
                evidence=occurrence_evidence or category_evidence,
            )
        )

    return CategoryResult(
        category="code_health",
        status=DataStatus.MEASURED,
        score=score,
        summary=f"Оценка чистоты кода: {score:.1f}/100. Обнаружено TODO: {todos}, FIXME: {fixmes}.",
        metrics=metrics,
        recommendations=tuple(recommendations),
    )


def _density_penalty(fixmes: int, todos: int, total_files: int) -> float:
    """Штраф в баллах: взвешенные маркеры, нормированные на число файлов."""
    weighted = fixmes * FIXME_PENALTY_PER_MARKER + todos * TODO_PENALTY_PER_MARKER
    return weighted / total_files * DENSITY_SCALE_TO_POINTS


def _score_for(fixmes: int, todos: int, total_files: int) -> float:
    return max(0.0, 100.0 - _density_penalty(fixmes, todos, total_files))


def _build_occurrence_evidence(occurrences: list[dict]) -> tuple[Evidence, ...]:
    """Превращает собранные вхождения маркеров в evidence с путём и строкой."""
    entries = []
    for occurrence in occurrences[:MAX_EVIDENCE_ENTRIES]:
        entries.append(
            Evidence(
                source="git_repository",
                reference=f"{occurrence['path']}:{occurrence['line']}",
                summary=f"{occurrence['kind']} на строке {occurrence['line']} "
                f"в файле {occurrence['path']}.",
            )
        )
    return tuple(entries)
