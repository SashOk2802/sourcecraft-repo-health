"""Категория Code health: плотность технического долга в комментариях кода.

Модуль разделён на две части. `collect` сканирует уже склонированную рабочую
область (LocalGitRepository передаётся из слоя оркестрации — сам модуль не
строит URL и не хранит токены) и возвращает факты. `evaluate` — чистая функция:
на одних и тех же фактах всегда даёт один и тот же результат.

TODO/FIXME считаются только в комментариях исходного кода: метки внутри
строковых литералов и JavaScript-регулярных выражений ложными не считаются.
Минифицированные и сгенерированные файлы пропускаются. Сканирование ограничено
бюджетом файлов и байтов; при превышении категория возвращает
`insufficient_sample` без балла и с объёмом уже просмотренных данных.
Возраст маркеров на shallow-клоне не вычисляется и показывается метрикой
измеренного результата, не подменяя оценку категории.
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
# Величины задокументированы в docs/scoring-methodology.md §4.3 (источник правды
# для экрана «Как считаем» — этот документ, а не только код).
FIXME_PENALTY_PER_MARKER = 5.0
TODO_PENALTY_PER_MARKER = 1.0
DENSITY_SCALE_TO_POINTS = 100.0
def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw.isdigit():
        value = int(raw)
        if value > 0:
            return value
    return default


# Максимум байт, читаемых из одного файла репозитория: файлы больше лимита
# пропускаются как «большие» (безопасное чтение через read_file_safe).
MAX_FILE_READ_BYTES = _env_int("SOURCECRAFT_CODE_HEALTH_MAX_FILE_READ_BYTES", 5 * 1024 * 1024)

# Порог, после которого рекомендация по TODO считается обоснованной.
TODO_RECOMMENDATION_THRESHOLD = 15

# Одинокий FIXME — ещё не подтверждённый критический дефект; жёсткая формулировка
# и P1 допустимы от этого числа маркеров.
FIXME_CRITICAL_COUNT = 2

# Верхняя граница evidence-записей по отдельным вхождениям маркеров: отчёт не
# должен раздуваться тысячами строк для репозиториев с огромным техдолгом.
MAX_EVIDENCE_ENTRIES = 20

# Бюджеты ресурсов сканирования (перенесены из codex/rebuild-file-analysis):
# лимит числа файлов-кандидатов и лимит суммарных прочитанных байт. Превышение
# бюджета означает insufficient_sample, а не частичный (заниженный) балл.
DEFAULT_MAX_SOURCE_FILES = _env_int("SOURCECRAFT_CODE_HEALTH_MAX_FILES", 50_000)
DEFAULT_MAX_TOTAL_SOURCE_BYTES = _env_int("SOURCECRAFT_CODE_HEALTH_MAX_BYTES", 100 * 1024 * 1024)

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
        "skipped_generated_files": 0,
        "bytes_read": 0,
        "candidate_files": 0,
        "truncated": False,
        "incomplete": False,
        "occurrences": [],
    }

    candidate_files = 0
    total_bytes = 0
    for root, dirs, files in os.walk(temp_dir):
        # Стабильный порядок нужен, чтобы обрезка бюджета выбирала один и тот же
        # префикс дерева, а не зависела от порядка каталога на диске.
        dirs[:] = sorted(name for name in dirs if name not in EXCLUDED_DIRECTORIES)
        for file in sorted(files):
            extension = os.path.splitext(file)[1].lower()
            if extension not in SUPPORTED_EXTENSIONS:
                continue
            if _is_generated_source(file):
                facts["skipped_generated_files"] += 1
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
                facts["incomplete"] = True
                continue
            if size > MAX_FILE_READ_BYTES:
                facts["skipped_large_files"] += 1
                facts["incomplete"] = True
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
                facts["incomplete"] = True
                continue

            marker_counts = _scan_comment_markers(
                content,
                extension,
                max_evidence=MAX_EVIDENCE_ENTRIES - len(facts["occurrences"]),
            )
            if marker_counts is None:
                # Неполный разбор нельзя считать «чистым»: весь результат
                # категории станет insufficient_sample.
                facts["incomplete"] = True
                continue

            todos, fixmes, occurrences = marker_counts
            facts["total_files"] += 1
            if todos == 0 and fixmes == 0:
                continue

            facts["todo_count"] += todos
            facts["fixme_count"] += fixmes
            facts["files_with_debt"] += 1
            facts["occurrences"].extend(
                {"path": relative_path, **occurrence} for occurrence in occurrences
            )

        if facts["truncated"]:
            break

    facts["bytes_read"] = total_bytes
    facts["candidate_files"] = candidate_files
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

    if raw_data.get("truncated") or raw_data.get("incomplete"):
        checked = raw_data.get("total_files", 0)
        bytes_read = raw_data.get("bytes_read", 0)
        candidates = raw_data.get("candidate_files", 0)
        unread = raw_data.get("incomplete") and not raw_data.get("truncated")
        return CategoryResult(
            category="code_health",
            status=DataStatus.INSUFFICIENT_SAMPLE,
            score=None,
            summary=(
                "Часть исходных файлов не прочитана, поэтому балл Code health не ставится."
                if unread
                else (
                    "Анализ исходного кода остановлен по лимиту ресурсов: "
                    f"разобрано {checked} файлов, прочитано {bytes_read} байт, "
                    f"кандидатов встречено {candidates}."
                )
            ),
            reason=(
                "code_health_unreadable_source" if unread else "code_health_scan_limit_exceeded"
            ),
            metrics=(
                MetricResult(
                    code="partial_analyzed_files",
                    value=checked,
                    normalized_score=None,
                    summary="Файлов разобрано до достижения лимита",
                ),
                MetricResult(
                    code="partial_bytes_read",
                    value=bytes_read,
                    normalized_score=None,
                    summary="Байт исходников прочитано до достижения лимита",
                ),
                MetricResult(
                    code="partial_candidate_files",
                    value=candidates,
                    normalized_score=None,
                    summary="Файлов-кандидатов встречено до остановки",
                ),
                MetricResult(
                    code="skipped_large_files",
                    value=raw_data.get("skipped_large_files", 0),
                    normalized_score=None,
                    summary="Файлов пропущено из-за размера больше 1 МиБ",
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

    files_with_debt = raw_data.get("files_with_debt", 0)
    occurrence_evidence = _build_occurrence_evidence(raw_data.get("occurrences", []))
    category_evidence = (
        Evidence(
            source="git_repository",
            reference=context.commit_sha,
            summary=f"Найдено {todos} TODO и {fixmes} FIXME в комментариях "
            f"{files_with_debt} {_file_count_word(files_with_debt)}.",
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
        # Доля файлов с долгом: информационная метрика (как total_analyzed_files),
        # а не вход формулы — нормализация отсутствует намеренно (A.1).
        MetricResult(
            code="code_health.debt_file_ratio",
            value=files_with_debt / total_files,
            normalized_score=None,
            summary="Доля файлов с техническим долгом (files_with_debt / total_files)",
        ),
        MetricResult(
            code="code_health.marker_age",
            value=None,
            normalized_score=None,
            summary=MARKER_AGE_SUMMARY,
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
                problem=f"В комментариях скопилось избыточное количество меток TODO ({todos} шт.).",
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
        summary=(
            f"Оценка чистоты кода: {score:.1f}/100. "
            f"Обнаружено TODO: {todos}, FIXME: {fixmes}. {MARKER_AGE_SUMMARY}"
        ),
        metrics=metrics,
        recommendations=tuple(recommendations),
    )


# Возраст TODO/FIXME в v1 не вычисляется: клон всегда shallow (--depth 1),
# истории для blame нет. Ограничение видно в метрике измеренного результата
# и не подменяет оценку плотности отдельным CategoryResult.
MARKER_AGE_SUMMARY = "Возраст TODO/FIXME недоступен: клон shallow, истории для blame нет."


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


def _file_count_word(count: int) -> str:
    """Склонение «файл» в предложном падеже после числа: 1 файле, 2+ файлах."""
    if count % 10 == 1 and count % 100 != 11:
        return "файле"
    return "файлах"


def _is_generated_source(filename: str) -> bool:
    """Минифицированные и сгенерированные файлы не входят в плотность долга."""
    name = filename.lower()
    stem, _extension = os.path.splitext(name)
    return stem.endswith((".min", ".generated")) or ".generated." in name


def _validate_resource_limit(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be positive")


def _scan_comment_markers(
    content: str,
    extension: str,
    *,
    max_evidence: int,
) -> tuple[int, int, list[dict[str, int | str]]] | None:
    """Считает маркеры без накопления всего текста комментариев в памяти.

    Каждый файл ограничен по размеру до вызова этой функции. Здесь дополнительно
    ограничен результат: хранятся только первые ``max_evidence`` вхождений.
    Полное число TODO/FIXME считается потоком и не создаёт список совпадений.
    """
    if extension == ".py":
        try:
            chunks = (
                (token.string, token.start[0])
                for token in tokenize.generate_tokens(io.StringIO(content).readline)
                if token.type == tokenize.COMMENT
            )
            return _count_marker_chunks(chunks, max_evidence=max_evidence)
        except (tokenize.TokenError, SyntaxError):
            return None

    return _count_marker_chunks(
        _iter_c_style_comment_chunks(
            content,
            rust=extension == ".rs",
            cpp=extension == ".cpp",
        ),
        max_evidence=max_evidence,
    )


def _count_marker_chunks(
    chunks,
    *,
    max_evidence: int,
) -> tuple[int, int, list[dict[str, int | str]]]:
    todos = 0
    fixmes = 0
    occurrences: list[dict[str, int | str]] = []

    for text, start_line in chunks:
        todos += sum(1 for _ in TODO_PATTERN.finditer(text))
        fixmes += sum(1 for _ in FIXME_PATTERN.finditer(text))
        _append_marker_evidence(
            occurrences,
            text,
            start_line,
            TODO_PATTERN,
            "TODO",
            max_evidence=max_evidence,
        )
        _append_marker_evidence(
            occurrences,
            text,
            start_line,
            FIXME_PATTERN,
            "FIXME",
            max_evidence=max_evidence,
        )

    return todos, fixmes, occurrences


def _append_marker_evidence(
    occurrences: list[dict[str, int | str]],
    text: str,
    start_line: int,
    pattern: re.Pattern[str],
    kind: str,
    *,
    max_evidence: int,
) -> None:
    """Добавляет только нужное число evidence и линейно вычисляет номера строк."""
    if len(occurrences) >= max_evidence:
        return

    line = start_line
    previous_end = 0
    for match in pattern.finditer(text):
        line += text.count("\n", previous_end, match.start())
        occurrences.append({"kind": kind, "line": line})
        if len(occurrences) >= max_evidence:
            return
        previous_end = match.end()


def _iter_c_style_comment_chunks(content: str, *, rust: bool, cpp: bool):
    """Поток комментариев C-подобных языков без квадратичного подсчёта строк."""
    index = 0
    line = 1
    length = len(content)

    while index < length:
        if content.startswith("//", index):
            end = content.find("\n", index + 2)
            if end == -1:
                yield content[index + 2 :], line
                return
            yield content[index + 2 : end], line
            index = end + 1
            line += 1
            continue

        if content.startswith("/*", index):
            end = content.find("*/", index + 2)
            if end == -1:
                text = content[index + 2 :]
                yield text, line
                return
            text = content[index + 2 : end]
            yield text, line
            line += text.count("\n")
            index = end + 2
            continue

        if rust and content[index] == "r":
            raw_end = _skip_rust_raw_string(content, index)
            if raw_end > index:
                line += content.count("\n", index, raw_end)
                index = raw_end
                continue

        if cpp and content.startswith('R"', index):
            raw_end = _skip_cpp_raw_string(content, index)
            if raw_end > index:
                line += content.count("\n", index, raw_end)
                index = raw_end
                continue

        character = content[index]
        if character in "\"'`":
            index, line = _skip_quoted_literal_with_line(content, index, line)
            continue
        if character == "\n":
            line += 1
        index += 1


def _skip_cpp_raw_string(content: str, index: int) -> int:
    """Пропускает C++ raw string вида R"delimiter(... )delimiter"."""
    delimiter_end = content.find("(", index + 2, index + 19)
    if delimiter_end == -1:
        return index
    delimiter = content[index + 2 : delimiter_end]
    if any(character.isspace() or character in "\\()\\" for character in delimiter):
        return index
    closing = ")" + delimiter + '"'
    end = content.find(closing, delimiter_end + 1)
    return index if end == -1 else end + len(closing)


def _skip_quoted_literal_with_line(content: str, index: int, line: int) -> tuple[int, int]:
    quote = content[index]
    index += 1
    while index < len(content):
        character = content[index]
        if character == "\\":
            if index + 1 < len(content) and content[index + 1] == "\n":
                line += 1
            index += 2
            continue
        if character == quote:
            return index + 1, line
        if character == "\n":
            line += 1
        index += 1
    return index, line


def _comment_spans(content: str, extension: str) -> list[tuple[str, int]] | None:
    """Комментарии файла как пары (текст, номер строки начала в исходнике).

    Для Python-файлов комментарии берутся из токенизатора, поэтому метки внутри
    строковых литералов ложными не считаются. ``None`` возвращается, если файл
    не удалось разобрать целиком: незакрытая строка — ``tokenize.TokenError``,
    битые отступы — ``IndentationError``/``TabError`` (оба — подклассы
    ``SyntaxError``, одного перехвата достаточно). Такой файл пропускается
    сканом целиком (CH.1, CH.2).
    """
    if extension == ".py":
        try:
            return [
                (token.string, token.start[0])
                for token in tokenize.generate_tokens(io.StringIO(content).readline)
                if token.type == tokenize.COMMENT
            ]
        except (tokenize.TokenError, SyntaxError):
            return None
    return _c_style_comments(
        content,
        javascript=extension in {".js", ".ts", ".jsx", ".tsx"},
        rust=extension == ".rs",
    )


def _marker_occurrences(
    relative_path: str,
    spans: list[tuple[str, int]],
    pattern: re.Pattern[str],
    kind: str,
) -> list[dict]:
    """Вхождения маркера в комментариях: kind, путь и строка (для evidence)."""
    occurrences: list[dict] = []
    for text, line in spans:
        for match in pattern.finditer(text):
            marker_line = line + text.count("\n", 0, match.start())
            occurrences.append({"kind": kind, "path": relative_path, "line": marker_line})
    return occurrences


def _c_style_comments(
    content: str, *, javascript: bool = False, rust: bool = False
) -> list[tuple[str, int]]:
    """C-style комментарии как (текст, стартовая строка), без строк и regex-литералов.

    Для Rust дополнительно пропускаются raw-строки ``r"..."``, ``r#"..."#`` и
    ``r##"..."##``: иначе ``// TODO`` внутри такого литерала читался бы как
    настоящий комментарий (false positive, NICE.1).
    """

    if javascript:
        return _javascript_comments(content)

    comments: list[tuple[str, int]] = []
    index = 0
    while index < len(content):
        if content.startswith("//", index):
            end = content.find("\n", index)
            text = content[index + 2 :] if end == -1 else content[index + 2 : end]
            comments.append((text, content.count("\n", 0, index) + 1))
            index = len(content) if end == -1 else end + 1
        elif content.startswith("/*", index):
            end = content.find("*/", index + 2)
            text = content[index + 2 :] if end == -1 else content[index + 2 : end]
            comments.append((text, content.count("\n", 0, index) + 1))
            index = len(content) if end == -1 else end + 2
        elif rust and content[index] == "r":
            raw_end = _skip_rust_raw_string(content, index)
            index = raw_end if raw_end > index else index + 1
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


def _javascript_comments(content: str) -> list[tuple[str, int]]:
    """Контекстный tokenizer для JS/TS-комментариев и regex-литералов.

    Возвращает комментарии как (текст, стартовая строка). ``/`` после обычной
    ``)`` означает деление, а после закрытия условия ``if (...)`` может
    начинаться regex-выражение. Стек скобок сохраняет этот контекст и для
    вложенных выражений. Отдельно отслеживается начало ESM-декларации: в
    ``export default <expression>`` после ``default`` также может начинаться
    regex-литерал.
    """

    comments: list[tuple[str, int]] = []
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
            text = content[index + 2 :] if end == -1 else content[index + 2 : end]
            comments.append((text, content.count("\n", 0, index) + 1))
            index = len(content) if end == -1 else end + 1
            continue
        if content.startswith("/*", index):
            end = content.find("*/", index + 2)
            text = content[index + 2 :] if end == -1 else content[index + 2 : end]
            comments.append((text, content.count("\n", 0, index) + 1))
            index = len(content) if end == -1 else end + 2
            continue

        character = content[index]
        if character == "`":
            # Template literal: текст между бэктиками — не комментарий, но
            # интерполяции ${...} — это код, где реальные // /* */ комментарии
            # обязаны находиться (false negative до фикса, NICE.1).
            index = _skip_template_literal(content, index, comments)
            expects_expression = False
            last_token = "literal"
            continue
        if character in "\"'":
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


def _skip_rust_raw_string(content: str, index: int) -> int:
    """Пропускает Rust raw-строку от позиции ``r``: r"…" / r#"…"# / r##"…"##.

    Возвращает позицию после закрывающего ``"#*`` либо ``index``, если это не
    raw-строка (обычный идентификатор, начинающийся с ``r``).
    """
    position = index + 1
    hashes = 0
    while position < len(content) and content[position] == "#":
        hashes += 1
        position += 1
    if position >= len(content) or content[position] != '"':
        return index
    delimiter = '"' + "#" * hashes
    end = content.find(delimiter, position + 1)
    if end == -1:
        return len(content)
    return end + len(delimiter)


def _skip_template_literal(
    content: str, backtick_index: int, comments: list[tuple[str, int]]
) -> int:
    """Сканирует template literal; интерполяции ``${...}`` обрабатываются как код."""
    index = backtick_index + 1
    while index < len(content):
        character = content[index]
        if character == "\\":
            index += 2
            continue
        if character == "`":
            return index + 1
        if character == "$" and content.startswith("${", index):
            index = _scan_interpolation(content, index + 2, comments)
            continue
        index += 1
    return index


def _scan_interpolation(
    content: str, start: int, comments: list[tuple[str, int]]
) -> int:
    """Проходит ``${...}`` как код, извлекая комментарии; возвращает после ``}``."""
    index = start
    depth = 1
    while index < len(content) and depth > 0:
        if content.startswith("//", index):
            end = content.find("\n", index)
            text = content[index + 2 :] if end == -1 else content[index + 2 : end]
            comments.append((text, content.count("\n", 0, index) + 1))
            index = len(content) if end == -1 else end + 1
            continue
        if content.startswith("/*", index):
            end = content.find("*/", index + 2)
            text = content[index + 2 :] if end == -1 else content[index + 2 : end]
            comments.append((text, content.count("\n", 0, index) + 1))
            index = len(content) if end == -1 else end + 2
            continue
        character = content[index]
        if character in "\"'`":
            index = _skip_quoted_literal(content, index)
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index + 1
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
