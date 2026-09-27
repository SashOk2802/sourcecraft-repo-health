"""Категория Code health: плотность технического долга в коде репозитория.

Модуль разделён на две части. `collect` сканирует уже склонированную рабочую
область (LocalGitRepository передаётся из слоя оркестрации — сам модуль не
строит URL и не хранит токены) и возвращает факты. `evaluate` — чистая функция:
на одних и тех же фактах всегда даёт один и тот же результат.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    Evidence,
    MetricResult,
    Recommendation,
    RecommendationPriority,
)
from backend.app.integrations.git_repository import (
    MAX_FILE_BYTES,
    GitCloneError,
    LocalGitRepository,
)

SUPPORTED_EXTENSIONS = frozenset({
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
})

# Директории, содержимое которых не является исходным кодом проекта.
EXCLUDED_DIRECTORIES = frozenset({
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
})

# Плотность долга: штрафные баллы за маркер, нормированные на число файлов.
# Величины задокументированы в docs/scoring-methodology.md §5 (источник правды
# для экрана «Как считаем» — этот документ, а не только код).
FIXME_PENALTY_PER_MARKER = 5.0
TODO_PENALTY_PER_MARKER = 1.0
DENSITY_SCALE_TO_POINTS = 100.0

# Лимиты защищают worker от репозитория с чрезмерным числом файлов или
# содержимым, которое суммарно не помещается в бюджет одного анализа.
DEFAULT_MAX_SOURCE_FILES = 20_000
DEFAULT_MAX_TOTAL_SOURCE_BYTES = 50 * 1024 * 1024

# Порог, после которого рекомендация по TODO считается обоснованной.
TODO_RECOMMENDATION_THRESHOLD = 15

# Одинокий FIXME — ещё не подтверждённый критический дефект; жёсткая формулировка
# и P1 допустимы от этого числа маркеров.
FIXME_CRITICAL_COUNT = 2

MAX_EVIDENCE_ENTRIES = 20

TODO_PATTERN = re.compile(r"\btodo\b", re.IGNORECASE)
FIXME_PATTERN = re.compile(r"\bfixme\b", re.IGNORECASE)

_JS_CONTROL_PAREN_KEYWORDS = frozenset({"catch", "for", "if", "switch", "while", "with"})
_JS_EXPRESSION_PREFIX_KEYWORDS = frozenset({
    "await",
    "case",
    "delete",
    "do",
    "else",
    "extends",
    "in",
    "instanceof",
    "new",
    "of",
    "return",
    "throw",
    "typeof",
    "void",
    "yield",
})
_JAVASCRIPT_EXTENSIONS = frozenset({".js", ".jsx", ".ts", ".tsx"})


def collect(
    repo: LocalGitRepository,
    *,
    max_files: int = DEFAULT_MAX_SOURCE_FILES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_SOURCE_BYTES,
) -> dict:
    """Сканирует комментарии исходников в пределах бюджета файлов и байтов.

    В blobless-клоне размер проверяется *до* ``read_file``. Поэтому большой
    файл не будет скачан из Git только ради того, чтобы быть отвергнутым.
    Если данных о размере нет либо бюджет исчерпан, частичный результат не
    превращается в оценку: ``evaluate`` вернёт ``insufficient_sample``.
    """
    _validate_resource_limit(max_files, "max_files")
    _validate_resource_limit(max_total_bytes, "max_total_bytes")
    if repo.temp_dir is None:
        raise GitCloneError("Рабочая область git-репозитория не подготовлена.")

    facts: dict = {
        "total_files": 0,
        "todo_count": 0,
        "fixme_count": 0,
        "files_with_debt": 0,
        "occurrences": [],
        "skipped_large_files": 0,
        "truncated": False,
    }
    candidate_files = total_bytes = 0

    for relative_path in repo.iter_files(excluded_directories=EXCLUDED_DIRECTORIES):
        extension = Path(relative_path).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            continue
        candidate_files += 1
        if candidate_files > max_files:
            facts["truncated"] = True
            break
        size = repo.file_size(relative_path)
        if size is None:
            # Без размера нельзя доказать соблюдение бюджета до lazy-fetch.
            facts["truncated"] = True
            break
        if size > MAX_FILE_BYTES:
            facts["skipped_large_files"] += 1
            continue
        if total_bytes + size > max_total_bytes:
            facts["truncated"] = True
            break
        # Списываем размер до read_file: бинарный файл тоже расходует лимит.
        total_bytes += size
        content = repo.read_file(relative_path, max_bytes=MAX_FILE_BYTES)
        if content is None or "\x00" in content:
            continue

        facts["total_files"] += 1
        comments = _comments_only(content, extension)
        todos = len(TODO_PATTERN.findall(comments))
        fixmes = len(FIXME_PATTERN.findall(comments))
        if todos == 0 and fixmes == 0:
            continue

        facts["todo_count"] += todos
        facts["fixme_count"] += fixmes
        facts["files_with_debt"] += 1

    return facts


def _validate_resource_limit(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be positive")


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
    if raw_data.get("truncated"):
        return CategoryResult(
            category="code_health",
            status=DataStatus.INSUFFICIENT_SAMPLE,
            score=None,
            summary="Анализ исходного кода остановлен по лимиту ресурсов.",
            reason="code_health_scan_limit_exceeded",
            metrics=(
                MetricResult(
                    code="partial_analyzed_files",
                    value=raw_data.get("total_files", 0),
                    normalized_score=None,
                    summary="Файлов проверено до достижения лимита",
                ),
            ),
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


def _comments_only(content: str, extension: str) -> str:
    """Оставляет только комментарии, не принимая строки и regex за TODO."""

    if extension == ".py":
        try:
            return "\n".join(
                token.string
                for token in tokenize.generate_tokens(io.StringIO(content).readline)
                if token.type == tokenize.COMMENT
            )
        except tokenize.TokenError:
            return ""
    if extension in _JAVASCRIPT_EXTENSIONS:
        return "\n".join(_javascript_comments(content))
    return "\n".join(_c_style_comments(content))


def _c_style_comments(content: str) -> list[str]:
    """Извлекает C-style комментарии, не путая их со строками."""

    comments: list[str] = []
    index = 0
    while index < len(content):
        if content.startswith("//", index):
            end = content.find("\n", index)
            comments.append(content[index + 2:] if end == -1 else content[index + 2:end])
            index = len(content) if end == -1 else end + 1
        elif content.startswith("/*", index):
            end = content.find("*/", index + 2)
            comments.append(content[index + 2:] if end == -1 else content[index + 2:end])
            index = len(content) if end == -1 else end + 2
        elif content[index] in "\"'`":
            index = _skip_quoted_literal(content, index)
        else:
            index += 1
    return comments


def _javascript_comments(content: str) -> list[str]:
    """Контекстный tokenizer комментариев и regex-литералов в JS/TS.

    Упрощённый, но контекстный разбор отличает ``/`` как начало regex и как
    оператор деления. В частности, regex допустим после control-скобки,
    ``export default`` и ``extends``.
    """

    comments: list[str] = []
    paren_context: list[bool] = []
    brace_context: list[bool] = []
    index = 0
    expects_expression = True
    last_token: str | None = None
    module_export_pending = False

    while index < len(content):
        if content[index].isspace():
            index += 1
            continue
        if content.startswith("//", index):
            end = content.find("\n", index)
            comments.append(content[index + 2:] if end == -1 else content[index + 2:end])
            index = len(content) if end == -1 else end + 1
            continue
        if content.startswith("/*", index):
            end = content.find("*/", index + 2)
            comments.append(content[index + 2:] if end == -1 else content[index + 2:end])
            index = len(content) if end == -1 else end + 2
            continue

        character = content[index]
        if character in "\"'`":
            index = _skip_quoted_literal(content, index)
            expects_expression = False
            last_token = "literal"
            continue
        if content.startswith("=>", index):
            index += 2
            expects_expression = True
            last_token = "arrow"
            continue
        if content.startswith(("++", "--"), index):
            index += 2
            last_token = "update"
            continue
        if character == "/":
            if expects_expression and (regex_end := _skip_js_regex(content, index)) is not None:
                index = regex_end
                expects_expression = False
                last_token = "regex"
                continue
            index += 2 if content.startswith("/=", index) else 1
            expects_expression = True
            last_token = "/"
            continue
        if _is_js_identifier_start(character):
            word_end = index + 1
            while word_end < len(content) and _is_js_identifier_part(content[word_end]):
                word_end += 1
            word = content[index:word_end]
            is_keyword = last_token != "."
            if is_keyword and word == "export":
                module_export_pending = True
                expects_expression = True
                last_token = word
            elif is_keyword and word == "default" and module_export_pending:
                module_export_pending = False
                expects_expression = True
                last_token = "export_default"
            else:
                module_export_pending = False
                last_token = word if is_keyword else "identifier"
                expects_expression = is_keyword and word in _JS_EXPRESSION_PREFIX_KEYWORDS
            index = word_end
            continue
        if character.isdigit():
            index += 1
            while index < len(content) and (content[index].isalnum() or content[index] in "._"):
                index += 1
            expects_expression = False
            last_token = "number"
            continue
        if character == "(":
            paren_context.append(last_token in _JS_CONTROL_PAREN_KEYWORDS)
            expects_expression = True
        elif character == ")":
            closes_control = paren_context.pop() if paren_context else False
            expects_expression = closes_control
            last_token = "control_paren_end" if closes_control else ")"
            index += 1
            continue
        elif character == "{":
            is_block = (
                last_token is None
                or not expects_expression
                or last_token in {"arrow", "control_paren_end", "do", "else", "finally", "try"}
            )
            brace_context.append(is_block)
            expects_expression = True
        elif character == "}":
            closes_block = brace_context.pop() if brace_context else False
            expects_expression = closes_block
            last_token = "block_end" if closes_block else "object_end"
            index += 1
            continue
        elif character == "]":
            expects_expression = False
        elif character in "([,;:?=+*-!~%&|^<>":
            expects_expression = True
        elif character == ".":
            expects_expression = False
        if character == ";":
            module_export_pending = False
        last_token = character
        index += 1

    return comments


def _skip_quoted_literal(content: str, quote_index: int) -> int:
    quote = content[quote_index]
    index = quote_index + 1
    while index < len(content):
        if content[index] == "\\\\":
            index += 2
        elif content[index] == quote:
            return index + 1
        else:
            index += 1
    return index


def _is_js_identifier_start(character: str) -> bool:
    return character in "_$" or character.isalpha()


def _is_js_identifier_part(character: str) -> bool:
    return _is_js_identifier_start(character) or character.isdigit()


def _skip_js_regex(content: str, slash_index: int) -> int | None:
    """Возвращает позицию после ``/.../flags`` или ``None`` для незакрытого regex."""

    index = slash_index + 1
    in_character_class = False
    while index < len(content):
        character = content[index]
        if character in "\r\n":
            return None
        if character == "\\\\":
            index += 2
            continue
        if character == "[":
            in_character_class = True
        elif character == "]" and in_character_class:
            in_character_class = False
        elif character == "/" and not in_character_class:
            index += 1
            while index < len(content) and content[index].isalpha():
                index += 1
            return index
        index += 1
    return None
