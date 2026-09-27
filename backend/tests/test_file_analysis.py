import os
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from backend.app.analyzers.code_health import (
    FIXME_CRITICAL_COUNT,
    MAX_FILE_READ_BYTES,
)
from backend.app.analyzers.code_health import (
    collect as ch_collect,
)
from backend.app.analyzers.code_health import (
    evaluate as ch_evaluate,
)
from backend.app.analyzers.code_health import (
    marker_age_status as ch_marker_age_status,
)
from backend.app.analyzers.documentation import (
    collect as doc_collect,
)
from backend.app.analyzers.documentation import (
    evaluate as doc_evaluate,
)
from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    RecommendationPriority,
    RepositoryRef,
)
from backend.app.integrations.git_repository import GitCloneError, LocalGitRepository


@pytest.fixture
def mock_context():
    """Создает валидный AnalysisContext согласно строгим контрактам."""
    repo_ref = RepositoryRef(
        id="test_repo_id",
        organization_slug="test_org",
        repository_slug="test_repo",
        web_url="https://github.com",
    )
    # Используем современный алиас UTC по требованию правила UP017
    now = datetime.now(UTC)
    return AnalysisContext(
        repository=repo_ref,
        commit_sha="abcdef1234567890",
        analyzed_at=now,
        period_start=now,
        period_end=now,
    )


def mock_repo(temp_dir="/fake_repo"):
    """Фейковый LocalGitRepository: только temp_dir для чтения фактов."""
    repo = MagicMock(spec=LocalGitRepository)
    repo.temp_dir = temp_dir
    return repo


def make_real_repo(tmp_path) -> LocalGitRepository:
    """Реальный LocalGitRepository поверх временной папки без git-вызовов."""
    repo = LocalGitRepository(repo_url="https://example.invalid/repo.git")
    repo.temp_dir = str(tmp_path)
    return repo


# --- code_health: collect ---------------------------------------------------


def test_code_health_collect_counts_markers_and_files(tmp_path):
    """Проверяет корректность сбора фактов о TODO/FIXME (4.6, 4.9)."""
    (tmp_path / "main.py").write_text(
        "def test():\n    # todo: fix this\n    # FIXME: critical defect",
        encoding="utf-8",
    )
    (tmp_path / "script.js").write_text("console.log('clean code');", encoding="utf-8")
    (tmp_path / "styles.css").write_text("body { color: red; }", encoding="utf-8")

    facts = ch_collect(make_real_repo(tmp_path))

    assert facts["total_files"] == 2
    assert facts["todo_count"] == 1  # lowercase todo считается (4.9)
    assert facts["fixme_count"] == 1
    assert facts["files_with_debt"] == 1
    assert len(facts["occurrences"]) == 2  # 4.6: по одному на каждый маркер
    assert facts["occurrences"][0]["path"] == "main.py"
    assert facts["occurrences"][0]["line"] == 2


def test_code_health_collect_skips_excluded_directories(tmp_path):
    """TODO/FIXME внутри node_modules и .git не влияют на счёт (4.7)."""
    (tmp_path / "app.py").write_text("# TODO: fix", encoding="utf-8")
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "dep.js").write_text("// FIXME: secret", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("# TODO: remove", encoding="utf-8")

    facts = ch_collect(make_real_repo(tmp_path))

    assert facts["total_files"] == 1
    assert facts["todo_count"] == 1
    assert facts["fixme_count"] == 0


def test_code_health_collect_counts_tsx_jsx_rs(tmp_path):
    """Расширения .tsx/.jsx/.rs входят в поддерживаемые (4.8)."""
    (tmp_path / "component.tsx").write_text("// TODO: split", encoding="utf-8")
    (tmp_path / "index.jsx").write_text("// FIXME: rename", encoding="utf-8")
    (tmp_path / "lib.rs").write_text("// todo: document", encoding="utf-8")

    facts = ch_collect(make_real_repo(tmp_path))

    assert facts["total_files"] == 3
    assert facts["todo_count"] == 2
    assert facts["fixme_count"] == 1


def test_code_health_collect_lowercase_markers_counted(tmp_path):
    """Нижний регистр todo/fixme учитывается так же, как верхний (4.9)."""
    (tmp_path / "app.py").write_text(
        "# todo: first\n# TODO: second\n# fixme: third\n", encoding="utf-8"
    )

    facts = ch_collect(make_real_repo(tmp_path))

    assert facts["todo_count"] == 2
    assert facts["fixme_count"] == 1


def test_code_health_collect_skips_file_with_indentation_error(tmp_path):
    """Файл с битыми отступами не роняет категорию и не считается в total_files (CH.1).

    Смесь таба и пробелов в одном блоке — реальный TabError (подкласс SyntaxError,
    а не TokenError): раньше он пробивал except и валил весь collect()/провайдер.
    """
    (tmp_path / "broken.py").write_text(
        "def outer():\n\tif True:\n        pass\n",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("# TODO: real\n", encoding="utf-8")

    facts = ch_collect(make_real_repo(tmp_path))

    assert facts["total_files"] == 1  # broken.py исключён, а не посчитан
    assert facts["todo_count"] == 1
    assert facts["fixme_count"] == 0
    assert facts["files_with_debt"] == 1
    assert [occurrence["path"] for occurrence in facts["occurrences"]] == ["app.py"]


def test_code_health_all_malformed_files_yields_not_applicable(tmp_path, mock_context):
    """Только битые файлы: not_applicable, а не error/unavailable (CH.1).

    Исключённые файлы не учитываются в total_files, поэтому evaluate проходит
    существующую ветку total_files == 0 — статус не переизобретается.
    """
    (tmp_path / "broken.py").write_text(
        "def outer():\n\tif True:\n        pass\n",
        encoding="utf-8",
    )

    facts = ch_collect(make_real_repo(tmp_path))
    result = ch_evaluate(mock_context, facts)

    assert facts["total_files"] == 0
    assert result.status == DataStatus.NOT_APPLICABLE
    assert result.score is None


def test_code_health_rust_raw_string_marker_is_not_a_comment(tmp_path):
    """// TODO внутри Rust raw-строки r#"..."# — не комментарий (NICE.1).

    Generic C-style сканер раньше «выходил» из литерала на внутренней кавычке,
    после чего // внутри hashed raw-строки читался как настоящий комментарий
    (false positive).
    """
    (tmp_path / "raw.rs").write_text(
        'let s = r#"label "quoted // TODO: inside raw string"#;\n'
        "// FIXME: real comment\n",
        encoding="utf-8",
    )

    facts = ch_collect(make_real_repo(tmp_path))

    assert facts["total_files"] == 1
    assert facts["todo_count"] == 0  # маркер внутри raw-строки не считается
    assert facts["fixme_count"] == 1
    assert [occurrence["path"] for occurrence in facts["occurrences"]] == ["raw.rs"]
    assert facts["occurrences"][0]["line"] == 2


def test_code_health_typescript_template_interpolation_comment_found(tmp_path):
    """// FIXME внутри ${...} template literal находится (NICE.1).

    Весь backtick-литерал раньше пропускался как одна «строка», поэтому
    настоящий комментарий внутри интерполяции терялся (false negative);
    текст самого шаблона комментарием по-прежнему не считается.
    """
    (tmp_path / "view.ts").write_text(
        "const label = `prefix ${value // FIXME: interpolated comment\n"
        "} suffix`;\n"
        "const other = `plain // TODO: not a comment`;\n",
        encoding="utf-8",
    )

    facts = ch_collect(make_real_repo(tmp_path))

    assert facts["total_files"] == 1
    assert facts["fixme_count"] == 1
    assert facts["todo_count"] == 0  # TODO в тексте шаблона — не комментарий
    assert [occurrence["path"] for occurrence in facts["occurrences"]] == ["view.ts"]
    assert facts["occurrences"][0]["line"] == 1


def test_code_health_javascript_regex_markers_are_not_comments(tmp_path):
    """// TODO внутри JS regex-литералов — не комментарий (порт из codex/rebuild-file-analysis).

    Токенизатор учитывает контекст: regex после управляющих скобок и после
    ``export default`` не путается с делением, а настоящие комментарии при этом
    считаются. Фикстура перенесена из ветки codex/rebuild-file-analysis
    (единственная часть той ветки, которой не было в main-тестах).
    """
    (tmp_path / "regex.js").write_text(
        "const re = /[//] TODO/; // FIXME: real comment\n"
        "const block = /[/*] FIXME/;\n"
        "function build() { return /[//] TODO/; }\n"
        "if (ok) /[//] TODO/.test(value);\n"
        "if ((ok && check())) {} /[//] TODO/.test(value);\n"
        "export default /[//] TODO/;\n"
        "const grouped = (left + right) / divisor; // TODO: grouped division\n"
        "const objectRatio = {value: 2} / divisor; // TODO: object division\n"
        "const propertyRatio = obj.if(value) / divisor; // TODO: property call\n"
        "const ratio = left / right; // TODO: real comment\n",
        encoding="utf-8",
    )

    facts = ch_collect(make_real_repo(tmp_path))

    assert facts["total_files"] == 1
    assert facts["todo_count"] == 4  # только настоящие комментарии после деления
    assert facts["fixme_count"] == 1
    assert facts["files_with_debt"] == 1


def test_code_health_collect_token_error_file_is_excluded_not_silently_empty(tmp_path):
    """Файл с незакрытой строкой исключается, а не считается «чистым» (CH.2).

    Раньше TokenError возвращал '' — файл попадал в total_files с нулём маркеров,
    теряя реальный FIXME выше точки обрыва. Теперь файл исключается из скана
    целиком (вариант «a» ревьюера): он не входит в total_files и не даёт маркеров.
    """
    (tmp_path / "broken.py").write_text(
        "# FIXME: real defect above the break\n"
        'value = """unterminated triple-quoted string\n',
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("# TODO: ok\n", encoding="utf-8")

    facts = ch_collect(make_real_repo(tmp_path))

    assert facts["total_files"] == 1  # broken.py исключён из total_files
    assert facts["fixme_count"] == 0
    assert facts["todo_count"] == 1
    assert facts["files_with_debt"] == 1
    assert [occurrence["path"] for occurrence in facts["occurrences"]] == ["app.py"]


def test_code_health_collect_requires_cloned_workspace(mock_context):
    """collect без подготовленного клона — ошибка git-слоя, а не внутренняя (I.6)."""
    with pytest.raises(GitCloneError):
        ch_collect(mock_repo(temp_dir=None))


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_code_health_collect_ignores_symlink_outside_clone(tmp_path):
    """Симлинк наружу клона не даёт вклад в TODO/FIXME (R.3).

    Файл-симлинк встречается при os.walk, но read_file_safe отклоняет его по
    realpath-контролю границ temp_dir и возвращает None — collect пропускает
    файл и продолжает сканирование остальных.
    """
    outside = tmp_path.parent / "evil-outside.py"
    outside.write_text("# TODO: evil\n# FIXME: secret\n", encoding="utf-8")
    link = tmp_path / "evil.py"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlink creation not permitted")

    (tmp_path / "app.py").write_text("# TODO: real\n", encoding="utf-8")

    facts = ch_collect(make_real_repo(tmp_path))

    assert facts["total_files"] == 1  # только успешно прочитанный app.py
    assert facts["todo_count"] == 1  # только app.py дал маркер
    assert facts["fixme_count"] == 0  # evil.py наружу не прочитан
    assert facts["files_with_debt"] == 1
    assert [occurrence["path"] for occurrence in facts["occurrences"]] == ["app.py"]


def test_code_health_collect_skips_oversized_file_without_reading(tmp_path, monkeypatch):
    """Файл больше лимита пропускается целиком, read_file_safe не вызывается (R.2).

    Бюджетный скан отсекает крупные файлы на этапе getsize, не читая их с диска:
    маркеры такого файла не попадают в факты вовсе (и в total_files тоже).
    """
    repo = make_real_repo(tmp_path)
    big = tmp_path / "big.py"
    chunk = "x" * (128 * 1024)  # блок 128 KiB, пишем частями, не раздувая память
    with big.open("w", encoding="utf-8") as f:
        f.write("# TODO: near start\n")
        for _ in range(16):
            f.write(chunk)
        f.write("# FIXME: beyond limit\n")

    captured_max_bytes: list[int] = []
    original = LocalGitRepository.read_file_safe

    def spy(self, relative_path, max_bytes=MAX_FILE_READ_BYTES):
        captured_max_bytes.append(max_bytes)
        return original(self, relative_path, max_bytes=max_bytes)

    monkeypatch.setattr(LocalGitRepository, "read_file_safe", spy)

    facts = ch_collect(repo)

    assert captured_max_bytes == []  # большой файл вообще не читается
    assert facts["total_files"] == 0
    assert facts["todo_count"] == 0
    assert facts["fixme_count"] == 0
    assert facts["skipped_large_files"] == 1


# --- code_health: evaluate --------------------------------------------------


def test_code_health_evaluate_measured(mock_context):
    """Проверяет расчёт скоринга и формирование контракта CategoryResult."""
    # Плотность: (1*5 + 5*1) / 100 * 100 = 10 → score 90.
    raw_data = {"total_files": 100, "todo_count": 5, "fixme_count": 1, "files_with_debt": 2}

    result = ch_evaluate(mock_context, raw_data)

    assert isinstance(result, CategoryResult)
    assert result.category == "code_health"
    assert result.status == DataStatus.MEASURED
    assert result.score == 90.0
    assert len(result.metrics) == 4
    assert len(result.recommendations) == 1
    assert result.recommendations[0].code == "code_health_resolve_fixme"


def test_code_health_debt_file_ratio_metric(mock_context):
    """Доля файлов с долгом видна в CategoryResult.metrics (A.1).

    files_with_debt/total_files попадает в отчёт как информационная метрика
    без normalized_score, а не остаётся внутренним фактом collect().
    """
    raw_data = {"total_files": 10, "todo_count": 2, "fixme_count": 1, "files_with_debt": 3}

    result = ch_evaluate(mock_context, raw_data)

    ratio_metric = next(
        metric for metric in result.metrics if metric.code == "code_health.debt_file_ratio"
    )
    assert ratio_metric.value == pytest.approx(0.3)
    assert ratio_metric.normalized_score is None


def test_code_health_marker_age_is_insufficient_sample_not_fabricated():
    """Возраст маркеров на shallow-клоне — insufficient_sample, не фиктивная дата (B.1).

    Клон всегда --depth 1 (см. LocalGitRepository.clone), поэтому возраст
    TODO/FIXME честно недоступен: никакого числового значения, никакого нуля —
    только явный статус по паттерну code_health_scan_limit_exceeded.
    """
    result = ch_marker_age_status()

    assert result.status == DataStatus.INSUFFICIENT_SAMPLE
    assert result.score is None
    assert result.reason == "code_health_marker_age_insufficient_history"
    assert "недоступен" in result.summary
    assert not any(getattr(result, field, None) for field in ("value", "marker_age"))


def test_code_health_evaluate_no_supported_files_is_not_applicable(mock_context):
    """Ноль поддерживаемых файлов — not_applicable, а не unavailable (4.1)."""
    raw_data = {"total_files": 0, "todo_count": 0, "fixme_count": 0}
    result = ch_evaluate(mock_context, raw_data)

    assert result.status == DataStatus.NOT_APPLICABLE
    assert result.score is None


def test_code_health_zero_supported_extensions_is_not_applicable(tmp_path, mock_context):
    """Репозиторий без единого файла кода: not_applicable, не unavailable/error (TEST.1).

    End-to-end проверка ветки «ноль поддерживаемых расширений»: в клоне только
    README.md и styles.css, collect() находит ноль файлов кода, и evaluate()
    проходит существующую ветку total_files == 0 → NOT_APPLICABLE (4.1).
    """
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "styles.css").write_text("body { color: red; }\n", encoding="utf-8")

    facts = ch_collect(make_real_repo(tmp_path))
    result = ch_evaluate(mock_context, facts)

    assert facts["total_files"] == 0
    assert facts["todo_count"] == 0
    assert facts["fixme_count"] == 0
    assert result.status == DataStatus.NOT_APPLICABLE
    assert result.score is None


def test_code_health_evaluate_clone_error_is_error_not_low_score(mock_context):
    """Ошибка git-слоя: ERROR и score=None, а не низкий числовой балл (TEST.2).

    Провайдер передаёт evaluate() словарь с ключом "error" (см.
    providers._workspace_evaluator, перехват GitCloneError); контракт категории —
    ERROR и null вместо вычитания штрафов из нулевого total_files.
    """
    result = ch_evaluate(mock_context, {"error": "не удалось получить содержимое репозитория"})

    assert result.status == DataStatus.ERROR
    assert result.score is None


def test_documentation_evaluate_clone_error_is_error_not_low_score(mock_context):
    """Ошибка git-слоя у Documentation: ERROR и score=None (TEST.2)."""
    result = doc_evaluate(mock_context, {"error": "не удалось получить содержимое репозитория"})

    assert result.status == DataStatus.ERROR
    assert result.score is None


def test_code_health_density_depends_on_total_files(mock_context):
    """Одинаковые маркеры при разном total_files дают разный score (4.2)."""
    small = ch_evaluate(
        mock_context,
        {"total_files": 10, "todo_count": 5, "fixme_count": 1, "files_with_debt": 1},
    )
    large = ch_evaluate(
        mock_context,
        {"total_files": 1000, "todo_count": 5, "fixme_count": 1, "files_with_debt": 1},
    )

    assert small.score != large.score
    assert large.score > small.score


def test_code_health_small_repo_single_marker_density_semantics(mock_context):
    """Один FIXME не обнуляет категорию: знаменатель не меньше 10 файлов (§7.2)."""
    one_file = ch_evaluate(
        mock_context,
        {"total_files": 1, "todo_count": 0, "fixme_count": 1, "files_with_debt": 1},
    )
    three_files = ch_evaluate(
        mock_context,
        {"total_files": 3, "todo_count": 0, "fixme_count": 1, "files_with_debt": 1},
    )
    six_files = ch_evaluate(
        mock_context,
        {"total_files": 6, "todo_count": 0, "fixme_count": 1, "files_with_debt": 1},
    )
    large = ch_evaluate(
        mock_context,
        {"total_files": 100, "todo_count": 0, "fixme_count": 1, "files_with_debt": 1},
    )

    assert one_file.score == 50.0
    assert three_files.score == 50.0
    assert six_files.score == 50.0
    assert large.score == 95.0
    assert large.score > one_file.score


def test_code_health_metrics_are_informational_not_scored(mock_context):
    """Сырые счётчики не несут normalized_score (4.3)."""
    raw_data = {"total_files": 100, "todo_count": 5, "fixme_count": 1, "files_with_debt": 2}

    result = ch_evaluate(mock_context, raw_data)

    assert all(metric.normalized_score is None for metric in result.metrics)


def test_code_health_expected_delta_matches_penalty(mock_context):
    """expected_score_delta равен реальной разнице score до/после правки (4.4)."""
    # 40 TODO при 100 файлах: penalty = 40, score = 60; после удаления — 100.
    raw_data = {"total_files": 100, "todo_count": 40, "fixme_count": 0, "files_with_debt": 1}

    result = ch_evaluate(mock_context, raw_data)

    recommendation = next(
        r for r in result.recommendations if r.code == "code_health_clear_todos"
    )
    assert result.score == 60.0
    assert recommendation.expected_score_delta == pytest.approx(40.0)


def test_code_health_single_fixme_does_not_escalate_to_p1(mock_context):
    """Одинокий FIXME — без жёсткой P1-формулировки (4.5)."""
    raw_data = {"total_files": 100, "todo_count": 0, "fixme_count": 1, "files_with_debt": 1}

    result = ch_evaluate(mock_context, raw_data)

    recommendation = result.recommendations[0]
    assert recommendation.priority == RecommendationPriority.P2
    assert "опасный" not in recommendation.rationale


def test_code_health_fixme_critical_count_boundary(mock_context):
    """Граница FIXME_CRITICAL_COUNT = 2 зафиксирована тестом (V.2).

    Единичный FIXME — P2; от FIXME_CRITICAL_COUNT включительно рекомендация
    эскалируется до жёсткой P1 с формулировкой про опасный код. Тест закрепляет
    значение константы и поведение по обе стороны границы, чтобы порог из
    методики §7.2 был проверяемым (ревью V.2).
    """
    assert FIXME_CRITICAL_COUNT == 2

    def recommendation_for(fixme_count):
        result = ch_evaluate(
            mock_context,
            {
                "total_files": 100,
                "todo_count": 0,
                "fixme_count": fixme_count,
                "files_with_debt": 1,
            },
        )
        return result.recommendations[0]

    single = recommendation_for(FIXME_CRITICAL_COUNT - 1)
    assert single.priority == RecommendationPriority.P2
    assert "опасный" not in single.rationale

    at_threshold = recommendation_for(FIXME_CRITICAL_COUNT)
    assert at_threshold.priority == RecommendationPriority.P1
    assert "опасный" in at_threshold.rationale

    above_threshold = recommendation_for(FIXME_CRITICAL_COUNT + 1)
    assert above_threshold.priority == RecommendationPriority.P1
    assert "опасный" in above_threshold.rationale


def test_code_health_fixme_evidence_has_path_and_line(mock_context):
    """Evidence рекомендации по FIXME содержит путь и строку маркера (4.6)."""
    raw_data = {
        "total_files": 1,
        "todo_count": 0,
        "fixme_count": 1,
        "files_with_debt": 1,
        "occurrences": [{"kind": "FIXME", "path": "src/app.py", "line": 12}],
    }

    result = ch_evaluate(mock_context, raw_data)

    recommendation = result.recommendations[0]
    assert recommendation.evidence
    assert recommendation.evidence[0].reference == "src/app.py:12"
    assert "src/app.py" in recommendation.evidence[0].summary


def test_code_health_summary_counts_match_and_pluralize(mock_context):
    """Сводная evidence-запись: счётчики совпадают с метриками, склонение верно (CH.3).

    «1 файлов» — грамматическая ошибка; должно быть «1 файле». Числа в сводке
    обязаны совпадать с todo_count/fixme_count из метрик того же результата.
    """
    raw_data = {"total_files": 1, "todo_count": 0, "fixme_count": 1, "files_with_debt": 1}

    result = ch_evaluate(mock_context, raw_data)
    metrics = {metric.code: metric.value for metric in result.metrics}

    fixme = next(
        r for r in result.recommendations if r.code == "code_health_resolve_fixme"
    )
    summary = fixme.evidence[0].summary
    assert f"{metrics['todo_count']} TODO" in summary
    assert f"{metrics['fixme_count']} FIXME" in summary
    assert "1 файле" in summary
    assert "файлов" not in summary

    plural = ch_evaluate(
        mock_context,
        {"total_files": 2, "todo_count": 1, "fixme_count": 1, "files_with_debt": 2},
    )
    fixme_two = next(
        r for r in plural.recommendations if r.code == "code_health_resolve_fixme"
    )
    assert "2 файлах" in fixme_two.evidence[0].summary


def test_code_health_todo_threshold_recommendation(mock_context):
    """Порог todos > 15 управляет рекомендацией (T.4)."""
    below = ch_evaluate(
        mock_context,
        {"total_files": 1000, "todo_count": 10, "fixme_count": 0, "files_with_debt": 1},
    )
    above = ch_evaluate(
        mock_context,
        {"total_files": 1000, "todo_count": 20, "fixme_count": 0, "files_with_debt": 1},
    )

    assert not any(r.code == "code_health_clear_todos" for r in below.recommendations)
    assert any(r.code == "code_health_clear_todos" for r in above.recommendations)


# --- documentation: collect -------------------------------------------------


def test_documentation_collect_detects_run_section(mock_context):
    """Настоящий раздел запуска/тестов распознаётся (3.1, позитив)."""
    repo = mock_repo()
    repo.file_exists.side_effect = lambda path: path in ["README.md", "LICENSE"]
    repo.read_file.return_value = (
        "## How to run\n\n```bash\ndocker compose up\n```\n\n## Tests\n\n```bash\npytest\n```"
    )

    facts = doc_collect(repo)

    assert facts["has_readme"] is True
    assert facts["has_license"] is True
    assert facts["has_contributing"] is False
    assert facts["has_shortcuts"] is True


def test_documentation_collect_no_false_positive_for_latest_runtime(mock_context):
    """'latest' и 'runtime' без реального раздела не дают has_shortcuts (T.5)."""
    repo = mock_repo()
    repo.file_exists.side_effect = lambda path: path in ["README.md"]
    repo.read_file.return_value = (
        "## Latest features\n\nThe runtime supports hot reload for the latest version."
    )

    facts = doc_collect(repo)

    assert facts["has_readme"] is True
    assert facts["has_shortcuts"] is False


def test_documentation_collect_widened_paths(mock_context):
    """Дополнительные пути файлов регламентов учитываются (3.3)."""
    repo = mock_repo()
    repo.file_exists.side_effect = lambda path: path in [
        ".github/CODEOWNERS",
        "README.rst",
        "LICENCE",
    ]
    repo.read_file.return_value = ""

    facts = doc_collect(repo)

    assert facts["has_codeowners"] is True
    assert facts["has_readme"] is True
    assert facts["has_license"] is True


# --- documentation: evaluate ------------------------------------------------


def test_documentation_evaluate_deductions(mock_context):
    """Проверяет штрафы за отсутствие обязательных регламентов."""
    raw_data = {
        "has_readme": True,
        "has_shortcuts": True,
        "has_contributing": False,
        "has_license": False,
        "has_codeowners": False,
    }

    result = doc_evaluate(mock_context, raw_data)

    assert result.category == "documentation"
    assert result.status == DataStatus.MEASURED
    assert result.score == 50.0
    assert len(result.recommendations) == 3


def test_documentation_evaluate_no_readme_single_recommendation(mock_context):
    """Без README — одна рекомендация за эту причину, а не две (T.6, 3.2)."""
    raw_data = {
        "has_readme": False,
        "has_shortcuts": False,
        "has_contributing": True,
        "has_license": True,
        "has_codeowners": True,
    }

    result = doc_evaluate(mock_context, raw_data)

    codes = [recommendation.code for recommendation in result.recommendations]
    assert codes == ["doc_missing_has_readme"]
    assert result.score == 65.0


def test_documentation_missing_readme_evidence_names_the_file(mock_context):
    """Evidence по отсутствующему README называет конкретный файл (A.2)."""
    raw_data = {
        "has_readme": False,
        "has_shortcuts": False,
        "has_contributing": True,
        "has_license": True,
        "has_codeowners": True,
    }

    result = doc_evaluate(mock_context, raw_data)

    readme = next(r for r in result.recommendations if r.code == "doc_missing_has_readme")
    assert readme.evidence
    assert readme.evidence[0].reference == "README.md"
    assert "README.md" in readme.evidence[0].summary


def test_documentation_missing_shortcuts_evidence_names_readme(mock_context):
    """Evidence по отсутствующим инструкциям ссылается на README.md (A.2)."""
    raw_data = {
        "has_readme": True,
        "has_shortcuts": False,
        "has_contributing": True,
        "has_license": True,
        "has_codeowners": True,
    }

    result = doc_evaluate(mock_context, raw_data)

    shortcuts = next(
        r for r in result.recommendations if r.code == "doc_missing_has_shortcuts"
    )
    assert shortcuts.evidence
    assert shortcuts.evidence[0].reference == "README.md"
    assert "README.md" in shortcuts.evidence[0].summary
