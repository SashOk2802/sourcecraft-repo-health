import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from backend.app.analyzers.code_health import (
    MAX_FILE_READ_BYTES,
)
from backend.app.analyzers.code_health import (
    collect as ch_collect,
)
from backend.app.analyzers.code_health import (
    evaluate as ch_evaluate,
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


def test_code_health_collect_counts_markers_and_files():
    """Проверяет корректность сбора фактов о TODO/FIXME (4.6, 4.9)."""
    repo = mock_repo()
    repo.read_file_safe.side_effect = [
        "def test():\n    # todo: fix this\n    # FIXME: critical defect",
        "console.log('clean code');",
    ]
    with patch("os.walk") as mock_walk:
        mock_walk.return_value = [
            ("/fake_repo", [], ["main.py", "script.js", "styles.css"])
        ]

        facts = ch_collect(repo)

    assert facts["total_files"] == 2
    assert facts["todo_count"] == 1  # lowercase todo считается (4.9)
    assert facts["fixme_count"] == 1
    assert facts["files_with_debt"] == 1
    assert len(facts["occurrences"]) == 2  # 4.6: по одному на каждый маркер
    assert facts["occurrences"][0]["path"] == "main.py"
    assert facts["occurrences"][0]["line"] == 2
    # Все чтения идут через read_file_safe с явным байтовым лимитом (R.2).
    assert repo.read_file_safe.call_count == 2
    assert all(
        call.kwargs.get("max_bytes") == MAX_FILE_READ_BYTES
        for call in repo.read_file_safe.call_args_list
    )


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

    assert facts["total_files"] == 2  # оба файла встречены обходом директории
    assert facts["todo_count"] == 1  # только app.py дал маркер
    assert facts["fixme_count"] == 0  # evil.py наружу не прочитан
    assert facts["files_with_debt"] == 1
    assert [occurrence["path"] for occurrence in facts["occurrences"]] == ["app.py"]


def test_code_health_collect_does_not_read_oversized_file_fully(tmp_path, monkeypatch):
    """Файл больше лимита читается с ограничением, collect не зависает (R.2/R.3).

    Создаётся реальный файл ~2 MiB (больше MAX_FILE_READ_BYTES = 1 MiB), поверх
    read_file_safe ставится spy: лимит проверяется по вызову (mock), без
    выделения гигабайтных фикстур. Маркер за пределами лимита в факты не попадает.
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

    assert captured_max_bytes == [MAX_FILE_READ_BYTES]
    assert facts["total_files"] == 1
    assert facts["todo_count"] == 1  # маркер в начале файла найден
    assert facts["fixme_count"] == 0  # маркер за пределами лимита не прочитан


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
    assert len(result.metrics) == 3
    assert len(result.recommendations) == 1
    assert result.recommendations[0].code == "code_health_resolve_fixme"


def test_code_health_evaluate_no_supported_files_is_not_applicable(mock_context):
    """Ноль поддерживаемых файлов — not_applicable, а не unavailable (4.1)."""
    raw_data = {"total_files": 0, "todo_count": 0, "fixme_count": 0}
    result = ch_evaluate(mock_context, raw_data)

    assert result.status == DataStatus.NOT_APPLICABLE
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
    """Одинокий маркер в репозитории из 1–5 файлов обнуляет категорию (V.1).

    Плотность — задокументированная семантика формулы (методика §5.2, ревью V.1):
    штраф растёт как 1/total_files, поэтому один FIXME даёт penalty >= 100 при
    total_files <= 5 и score 0. Интерпретация ревью V.1 — «концентрированный
    долг в маленьком репо хуже», а не дефект нормировки. Тест фиксирует
    стабильность поведения для диапазона 1–3 файла (который старые тесты
    10/100/1000 не покрывали); корректность самого порога ожидает согласования
    владельцем методики (PENDING_APPROVAL в §5.2) и этим тестом не доказывается.
    """
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

    # penalty = (1*5)/1*100 = 500 → score 0
    assert one_file.score == 0.0
    # penalty = (1*5)/3*100 ≈ 166.67 → score 0
    assert three_files.score == 0.0
    # Градиент непрерывен: за пределами «обнуляющего» диапазона score > 0.
    assert six_files.score == pytest.approx(16.67, abs=0.01)
    assert six_files.score > three_files.score
    # В обычном по размеру репо одинокий FIXME категорию не обнуляет.
    assert large.score == 95.0


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
