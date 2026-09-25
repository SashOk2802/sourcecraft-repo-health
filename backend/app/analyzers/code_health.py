"""Оценка технического долга по TODO/FIXME только в комментариях исходного кода."""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Callable
from dataclasses import dataclass
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
from backend.app.integrations.git_repository import MAX_FILE_BYTES, LocalGitRepository

CATEGORY_CODE = "code_health"
SUPPORTED_EXTENSIONS = frozenset({".py", ".js", ".ts", ".go", ".java", ".cpp", ".cs"})
EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".venv", "__pycache__", "build", "coverage", "dist", "generated", "node_modules", "vendor", "venv",
})
_MARKER = re.compile(r"\b(TODO|FIXME)\b")
_JS_REGEX_PREFIX_CHARS = frozenset("([{:;,=!?&|+-*%^~<>")
_JS_REGEX_PREFIX_KEYWORDS = frozenset({
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
})


@dataclass(frozen=True, slots=True)
class CodeHealthFacts:
    total_files: int
    todo_count: int
    fixme_count: int
    files_with_debt: int
    skipped_large_files: int


def collect(repository: LocalGitRepository) -> CodeHealthFacts:
    """Анализирует малые исходники, исключая vendor, generated и бинарные файлы."""

    total = todos = fixmes = debt_files = skipped_large = 0
    for relative_path in repository.iter_files(excluded_directories=EXCLUDED_DIRECTORIES):
        if Path(relative_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        size = repository.file_size(relative_path)
        if size is None or size > MAX_FILE_BYTES:
            skipped_large += 1
            continue
        content = repository.read_file(relative_path)
        if content is None or "\x00" in content:
            continue
        total += 1
        markers = _MARKER.findall(_comments_only(content, Path(relative_path).suffix.lower()))
        todo = markers.count("TODO")
        fixme = markers.count("FIXME")
        todos += todo
        fixmes += fixme
        debt_files += int(todo > 0 or fixme > 0)
    return CodeHealthFacts(total, todos, fixmes, debt_files, skipped_large)


def evaluate(context: AnalysisContext, facts: CodeHealthFacts) -> CategoryResult:
    if facts.total_files == 0:
        return CategoryResult(
            CATEGORY_CODE, DataStatus.UNAVAILABLE, None,
            "Нет доступных поддерживаемых файлов исходного кода.", reason="code_files_unavailable",
        )
    penalty = facts.fixme_count * 5 + facts.todo_count
    score = float(max(0, 100 - penalty))
    evidence = (Evidence("repository_code", context.commit_sha, "TODO/FIXME посчитаны только в комментариях."),)
    metrics = (
        MetricResult("total_analyzed_files", facts.total_files, 100.0, "Проверено файлов кода"),
        MetricResult("todo_count", facts.todo_count, float(max(0, 100 - facts.todo_count)), "TODO в комментариях"),
        MetricResult("fixme_count", facts.fixme_count, float(max(0, 100 - facts.fixme_count * 5)), "FIXME в комментариях"),
    )
    recommendations: list[Recommendation] = []
    if facts.fixme_count:
        recommendations.append(Recommendation(
            "code_health_resolve_fixme", RecommendationPriority.P1,
            f"В комментариях осталось FIXME: {facts.fixme_count}.",
            "Устраните проблему или перенесите её в трекер задач.",
            "FIXME сигнализирует о незавершённой части кода.",
            expected_score_delta=float(min(100, facts.fixme_count * 5)), evidence=evidence,
        ))
    if facts.todo_count > 15:
        recommendations.append(Recommendation(
            "code_health_review_todos", RecommendationPriority.P3,
            f"В комментариях накопилось TODO: {facts.todo_count}.",
            "Проведите ревизию временных пометок.",
            "Избыточные TODO скрывают важные задачи.", expected_score_delta=5.0, evidence=evidence,
        ))
    return CategoryResult(CATEGORY_CODE, DataStatus.MEASURED, score,
                          f"Оценка чистоты кода: {score:.0f}/100.", metrics, tuple(recommendations))


def make_analyzer(facts_provider: Callable[[], CodeHealthFacts]) -> Callable[[AnalysisContext], CategoryResult]:
    return lambda context: evaluate(context, facts_provider())


def _comments_only(content: str, extension: str) -> str:
    if extension == ".py":
        try:
            return "\n".join(
                token.string for token in tokenize.generate_tokens(io.StringIO(content).readline)
                if token.type == tokenize.COMMENT
            )
        except tokenize.TokenError:
            return ""
    return "\n".join(
        _c_style_comments(content, javascript=extension in {".js", ".ts"})
    )


def _c_style_comments(content: str, *, javascript: bool = False) -> list[str]:
    """Небольшой lexer: строки и JS-regex не становятся комментариями."""

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
        elif javascript and content[index] == "/" and _can_start_js_regex(content, index):
            regex_end = _skip_js_regex(content, index)
            index = regex_end if regex_end is not None else index + 1
        else:
            index += 1
    return comments


def _can_start_js_regex(content: str, slash_index: int) -> bool:
    """Отличает начало regex-литерала от оператора деления.

    В JavaScript регулярное выражение может начинаться только там, где
    ожидается новое выражение: в начале файла, после оператора или
    после некоторых ключевых слов. После имени или числа ``/`` остаётся
    оператором деления.
    """

    previous = slash_index - 1
    while previous >= 0 and content[previous].isspace():
        previous -= 1
    if previous < 0:
        return True
    if content[previous] in _JS_REGEX_PREFIX_CHARS:
        return True
    if not (content[previous].isalnum() or content[previous] in "_$"):
        return False

    word_end = previous + 1
    while previous >= 0 and (content[previous].isalnum() or content[previous] in "_$"):
        previous -= 1
    return content[previous + 1:word_end] in _JS_REGEX_PREFIX_KEYWORDS


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
