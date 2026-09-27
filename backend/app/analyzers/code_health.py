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
DEFAULT_MAX_SOURCE_FILES = 20_000
DEFAULT_MAX_TOTAL_SOURCE_BYTES = 50 * 1024 * 1024
SUPPORTED_EXTENSIONS = frozenset({".py", ".js", ".ts", ".go", ".java", ".cpp", ".cs"})
EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".venv", "__pycache__", "build", "coverage", "dist", "generated", "node_modules", "vendor", "venv",
})
_MARKER = re.compile(r"\b(TODO|FIXME)\b")
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


@dataclass(frozen=True, slots=True)
class CodeHealthFacts:
    total_files: int
    todo_count: int
    fixme_count: int
    files_with_debt: int
    skipped_large_files: int
    truncated: bool = False

    def __post_init__(self) -> None:
        counts = (
            self.total_files,
            self.todo_count,
            self.fixme_count,
            self.files_with_debt,
            self.skipped_large_files,
        )
        if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counts):
            raise ValueError("code health counters must be non-negative integers")
        if not isinstance(self.truncated, bool):
            raise TypeError("code health truncated flag must be a boolean")


def collect(
    repository: LocalGitRepository,
    *,
    max_files: int = DEFAULT_MAX_SOURCE_FILES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_SOURCE_BYTES,
) -> CodeHealthFacts:
    """Анализирует исходники в пределах явного бюджета файлов и байтов."""

    _validate_resource_limit(max_files, "max_files")
    _validate_resource_limit(max_total_bytes, "max_total_bytes")

    total = todos = fixmes = debt_files = skipped_large = 0
    candidate_files = total_bytes = 0
    truncated = False
    for relative_path in repository.iter_files(excluded_directories=EXCLUDED_DIRECTORIES):
        if Path(relative_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        candidate_files += 1
        if candidate_files > max_files:
            truncated = True
            break
        size = repository.file_size(relative_path)
        # В blobless Git-дереве неизвестный размер нельзя безопасно читать:
        # Git пришлось бы скачать объект до проверки лимита. Останавливаем
        # анализ вместо частичной и потенциально опасной оценки.
        if size is None:
            truncated = True
            break
        if size > MAX_FILE_BYTES:
            skipped_large += 1
            continue
        if total_bytes + size > max_total_bytes:
            truncated = True
            break
        # Бюджет ограничивает байты, прочитанные с диска, а не только тексты,
        # которые позже прошли проверку. Бинарный файл с NUL тоже тратит лимит.
        total_bytes += size
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
    return CodeHealthFacts(total, todos, fixmes, debt_files, skipped_large, truncated)


def _validate_resource_limit(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be positive")


def evaluate(context: AnalysisContext, facts: CodeHealthFacts) -> CategoryResult:
    if facts.truncated:
        return CategoryResult(
            category=CATEGORY_CODE,
            status=DataStatus.INSUFFICIENT_SAMPLE,
            score=None,
            summary="Анализ исходного кода остановлен по лимиту ресурсов.",
            reason="code_health_scan_limit_exceeded",
            metrics=(
                MetricResult(
                    "partial_analyzed_files",
                    facts.total_files,
                    None,
                    "Файлов проверено до достижения лимита",
                ),
            ),
        )
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
    """Извлекает C-style комментарии, не путая их со строками."""

    if javascript:
        return _javascript_comments(content)

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
        else:
            index += 1
    return comments


def _javascript_comments(content: str) -> list[str]:
    """Контекстный tokenizer для JS/TS-комментариев и regex-литералов.

    ``/`` после обычной ``)`` означает деление, а после закрытия
    условия ``if (...)`` может начинать regex-выражение. Стек скобок
    сохраняет этот контекст и для вложенных выражений. Отдельно
    отслеживается начало ESM-декларации: в ``export default <expression>``
    после ``default`` также может начинаться regex-литерал.
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
                # контекст для следующего token. Это важно для конструкции
                # ``export default /.../``: regex начинается после default.
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
