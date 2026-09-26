"""Категория Code health: плотность технического долга в комментариях кода.

Модуль разделён на две части. `collect` сканирует уже склонированную рабочую
область (LocalGitRepository передаётся из слоя оркестрации — сам модуль не
строит URL и не хранит токены) и возвращает факты. `evaluate` — чистая функция:
на одних и тех же фактах всегда даёт один и тот же результат.

TODO/FIXME считаются **только в комментариях** исходного кода: метки внутри
строковых литералов и JavaScript-регулярных выражений ложными не считаются
(перенесено из codex/rebuild-file-analysis). Сканирование ограничено явным
бюджетом файлов и байтов; при превышении категория возвращает
`insufficient_sample`, а не частичный низкий балл.
"""

from __future__ import annotations

import io
import os
import re
import tokenize

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
# пропускаются как «большие» (безопасное чтение через read_file_safe).
MAX_FILE_READ_BYTES = 1_048_576

# Порог, после которого рекомендация по TODO считается обоснованной.
TODO_RECOMMENDATION_THRESHOLD = 15

# Одинокий FIXME — ещё не подтверждённый критический дефект; жёсткая формулировка
# и P1 допустимы от этого числа маркеров.
FIXME_CRITICAL_COUNT = 2

# Бюджеты ресурсов сканирования (перенесены из codex/rebuild-file-analysis):
# лимит числа файлов-кандидатов и лимит суммарных прочитанных байт. Превышение
# бюджета означает insufficient_sample, а не частичный (заниженный) балл.
DEFAULT_MAX_SOURCE_FILES = 20_000
DEFAULT_MAX_TOTAL_SOURCE_BYTES = 50 * 1024 * 1024

TODO_PATTERN = re.compile(r"\btodo\b", re.IGNORECASE)
FIXME_PATTERN = re.compile(r"\bfixme\b", re.IGNORECASE)

# Контекст JavaScript: после этих ключевых слов открывающая скобка — управляющая
# конструкция, и после её закрытия может начинаться regex-литерал (деление после
# обычной ``)`` от regex-литерала отличается именно этим контекстом).
_JS_CONTROL_PAREN_KEYWORDS = frozenset({"catch", "for", "if", "switch", "while", "with"})
_JS_EXPRESSION_PREFIX_KEYWORDS = frozenset(
    {
        "await",
        "case",
        "delete",
        "do",
        "else",
        "in",
        "instanceof",
        "new",
        "of",
        "return",
        "throw",
        "typeof",
        "void",
        "yield",
    }
)


def collect(
    repo: LocalGitRepository,
    *,
    max_files: int = DEFAULT_MAX_SOURCE_FILES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_SOURCE_BYTES,
) -> dict:
    """Сканирует клон на TODO/FIXME в комментариях и возвращает факты.

    Репозиторий уже аутентифицирован и склонирован вызывающей стороной
    (провайдер анализа); здесь выполняется только чтение файлов. Сканирование
    ограничено явным бюджетом: максимум файлов-кандидатов и максимум суммарных
    байт, прочитанных с диска. При превышении бюджета возвращается флаг
    ``truncated`` — evaluate превращает его в insufficient_sample, а не в
    частичный низкий балл.
    """
    _validate_resource_limit(max_files, "max_files")
    _validate_resource_limit(max_total_bytes, "max_total_bytes")

    temp_dir = repo.temp_dir
    if temp_dir is None:
        raise GitCloneError("Рабочая область git-репозитория не подготовлена.")

    facts: dict = {
        "total_files": 0,
        "todo_count": 0,
        "fixme_count": 0,
        "files_with_debt": 0,
        "skipped_large_files": 0,
        "truncated": False,
    }

    candidate_files = 0
    total_bytes = 0
    for root, dirs, files in os.walk(temp_dir):
        dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRECTORIES]
        for file in files:
            extension = os.path.splitext(file)[1].lower()
            if extension not in SUPPORTED_EXTENSIONS:
                continue

            candidate_files += 1
            if candidate_files > max_files:
                facts["truncated"] = True
                break

            file_path = os.path.join(root, file)
            try:
                size = os.path.getsize(file_path)
            except OSError:
                facts["skipped_large_files"] += 1
                continue
            if size > MAX_FILE_READ_BYTES:
                facts["skipped_large_files"] += 1
                continue
            if total_bytes + size > max_total_bytes:
                facts["truncated"] = True
                break
            # Бюджет учитывает байты, прочитанные с диска, а не только файлы,
            # которые позже прошли проверку (бинарный файл тоже тратит лимит).
            total_bytes += size

            relative_path = os.path.relpath(file_path, temp_dir).replace(os.sep, "/")
            content = repo.read_file_safe(relative_path, max_bytes=MAX_FILE_READ_BYTES)
            if content is None or "\x00" in content:
                continue

            comments = _comments_only(content, extension)
            todos = len(TODO_PATTERN.findall(comments))
            fixmes = len(FIXME_PATTERN.findall(comments))

            facts["total_files"] += 1
            if todos == 0 and fixmes == 0:
                continue

            facts["todo_count"] += todos
            facts["fixme_count"] += fixmes
            facts["files_with_debt"] += 1

        if facts["truncated"]:
            break

    return facts


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

    evidence = (
        Evidence(
            source="git_repository",
            reference=context.commit_sha,
            summary=f"Найдено {todos} TODO и {fixmes} FIXME в комментариях "
            f"{raw_data.get('files_with_debt', 0)} файлов.",
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
            summary="Количество меток TODO в комментариях кода",
        ),
        MetricResult(
            code="fixme_count",
            value=fixmes,
            normalized_score=None,
            summary="Количество критических меток FIXME в комментариях кода",
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
                problem=f"В комментариях присутствуют неразрешенные маркеры FIXME ({fixmes} шт.).",
                action="Устраните или закройте критические метки FIXME, перенеся их в таск-трекер.",
                rationale=rationale,
                expected_score_delta=float(max(0.0, delta_after - delta_before)),
                evidence=evidence,
            )
        )
    if todos > TODO_RECOMMENDATION_THRESHOLD:
        delta_before = score
        delta_after = _score_for(fixmes, 0, total_files)
        recommendations.append(
            Recommendation(
                code="code_health_clear_todos",
                priority=RecommendationPriority.P3,
                problem=f"В комментариях скопилось избыточное количество меток TODO ({todos} шт.).",
                action="Проведите ревизию кода и очистите его от неактуальных временных меток.",
                rationale="Слишком большое количество TODO замыливает глаз разработчикам.",
                expected_score_delta=float(max(0.0, delta_after - delta_before)),
                evidence=evidence,
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


def _validate_resource_limit(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be positive")


def _comments_only(content: str, extension: str) -> str:
    """Возвращает только комментарии файла; строки и regex-литералы исключаются."""
    if extension == ".py":
        try:
            return "\n".join(
                token.string
                for token in tokenize.generate_tokens(io.StringIO(content).readline)
                if token.type == tokenize.COMMENT
            )
        except tokenize.TokenError:
            return ""
    return "\n".join(
        _c_style_comments(content, javascript=extension in {".js", ".ts", ".jsx", ".tsx"})
    )


def _c_style_comments(content: str, *, javascript: bool = False) -> list[str]:
    """Извлекает C-style комментарии, не путая их со строками и regex-литералами."""

    if javascript:
        return _javascript_comments(content)

    comments: list[str] = []
    index = 0
    while index < len(content):
        if content.startswith("//", index):
            end = content.find("\n", index)
            comments.append(content[index + 2 :] if end == -1 else content[index + 2 : end])
            index = len(content) if end == -1 else end + 1
        elif content.startswith("/*", index):
            end = content.find("*/", index + 2)
            comments.append(content[index + 2 :] if end == -1 else content[index + 2 : end])
            index = len(content) if end == -1 else end + 2
        elif content[index] in "\"'`":
            quote = content[index]
            index += 1
            while index < len(content):
                if content[index] == "\\":
                    index += 2
                elif content[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
        else:
            index += 1
    return comments


def _javascript_comments(content: str) -> list[str]:
    """Контекстный tokenizer для JS/TS-комментариев и regex-литералов.

    ``/`` после обычной ``)`` означает деление, а после закрытия условия
    ``if (...)`` может начинаться regex-выражение. Стек скобок сохраняет этот
    контекст и для вложенных выражений. Отдельно отслеживается начало
    ESM-декларации: в ``export default <expression>`` после ``default`` также
    может начинаться regex-литерал.
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
            comments.append(content[index + 2 :] if end == -1 else content[index + 2 : end])
            index = len(content) if end == -1 else end + 1
            continue
        if content.startswith("/*", index):
            end = content.find("*/", index + 2)
            comments.append(content[index + 2 :] if end == -1 else content[index + 2 : end])
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
            # Префиксный update всё ещё ожидает выражение, постфиксный его завершает.
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
            # После точки reserved word является именем свойства: ``obj.if()``
            # не должен превращать обычную ``)`` в закрытие условия.
            is_keyword = last_token != "."
            if is_keyword and word == "export":
                # ``export`` сам по себе не является выражением, но задаёт
                # контекст для следующего token: в ``export default /.../``
                # regex начинается после default.
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
        if content[index] == "\\":
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
        if character == "\\":
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
