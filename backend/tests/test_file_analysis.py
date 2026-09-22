#!/usr/bin/env python
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from backend.app.analyzers.code_health import (
    collect as ch_collect,
    evaluate as ch_evaluate,
)
from backend.app.analyzers.documentation import (
    collect as doc_collect,
    evaluate as doc_evaluate,
)
from backend.app.contracts import (
    AnalysisContext,
    CategoryResult,
    DataStatus,
    RepositoryRef,
)


@pytest.fixture
def mock_context():
    """Создает валидный AnalysisContext согласно строгим контрактам."""
    repo_ref = RepositoryRef(
        id="test_repo_id",
        organization_slug="test_org",
        repository_slug="test_repo",
        web_url="https://github.com",
    )
    return AnalysisContext(
        repository=repo_ref,
        commit_sha="abcdef1234567890",
        analyzed_at=datetime.utcnow(),
        period_start=datetime.utcnow(),
        period_end=datetime.utcnow(),
    )


@patch("backend.app.analyzers.code_health.LocalGitRepository")
def test_code_health_collect_success(mock_repo_cls, mock_context):
    """Проверяет корректность сбора фактов о TODO/FIXME."""
    mock_repo = MagicMock()
    mock_repo.clone.return_value = "/tmp/fake_repo"
    mock_repo_cls.return_value = mock_repo

    # Симулируем обход дерева файлов
    with patch("os.walk") as mock_walk, patch("builtins.open", patch("builtins.open", create=True)) as mock_open:
        mock_walk.return_value = [("/tmp/fake_repo", [], ["main.py", "script.js", "styles.css"])]
        
        # Симулируем содержимое файлов (один с TODO и FIXME, один чистый)
        mock_file_ctx = MagicMock()
        mock_file_ctx.__enter__.return_value.read.side_effect = [
            "def test():\n    # TODO: fix this\n    # FIXME: critical defect",
            "console.log('clean code');"
        ]
        mock_open.return_value = mock_file_ctx

        facts = ch_collect(mock_context)

    assert facts["total_files"] == 2  # Стили .css должны проигнорироваться
    assert facts["todo_count"] == 1
    assert facts["fixme_count"] == 1
    assert facts["files_with_debt"] == 1


def test_code_health_evaluate_measured(mock_context):
    """Проверяет расчет скоринга и формирование контракта CategoryResult."""
    raw_data = {"total_files": 10, "todo_count": 5, "fixme_count": 1, "files_with_debt": 2}
    
    result = ch_evaluate(mock_context, raw_data)
    
    assert isinstance(result, CategoryResult)
    assert result.category == "code_health"
    assert result.status == DataStatus.MEASURED
    assert result.score == 90.0  # 100 - (1 * 5 + 5 * 1)
    assert len(result.metrics) == 3
    assert len(result.recommendations) == 1
    assert result.recommendations[0].code == "code_health_resolve_fixme"


def test_code_health_evaluate_unavailable(mock_context):
    """Проверяет уход в UNAVAILABLE, если файлов кода нет."""
    raw_data = {"total_files": 0, "todo_count": 0, "fixme_count": 0}
    result = ch_evaluate(mock_context, raw_data)
    
    assert result.status == DataStatus.UNAVAILABLE
    assert result.score is None


@patch("backend.app.analyzers.documentation.LocalGitRepository")
def test_documentation_collect(mock_repo_cls, mock_context):
    """Проверяет логику сбора фактов о наличии документации."""
    mock_repo = MagicMock()
    mock_repo.file_exists.side_effect = lambda path: path in ["README.md", "LICENSE"]
    mock_repo.read_file.return_value = "To run this application use: docker-compose up"
    mock_repo_cls.return_value = mock_repo

    facts = doc_collect(mock_context)
    
    assert facts["has_readme"] is True
    assert facts["has_license"] is True
    assert facts["has_contributing"] is False
    assert facts["has_shortcuts"] is True


def test_documentation_evaluate_deductions(mock_context):
    """Проверяет штрафы за отсутствие обязательных регламентов."""
    # Есть только README и инструкции, остальное отсутствует
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
    # 100 - 20(contributing) - 15(license) - 15(codeowners) = 50
    assert result.score == 50.0
    assert len(result.recommendations) == 3  # Три рекомендации на отсутствующие файлы
