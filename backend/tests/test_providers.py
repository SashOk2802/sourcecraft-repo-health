"""Тесты провайдеров анализаторов: регистрация, общий клон, жизненный цикл."""

import os
import subprocess
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from backend.app.analysis.providers import (
    project_life_analyzer_provider,
    repo_content_analyzer_provider,
)
from backend.app.analysis.runner import run_analysis
from backend.app.contracts import AnalysisContext, DataStatus, RepositoryRef
from backend.app.integrations.git_repository import GitCloneError


@pytest.fixture
def mock_context():
    now = datetime.now(UTC)
    return AnalysisContext(
        repository=RepositoryRef(
            id="repo-1",
            organization_slug="team",
            repository_slug="platform-api",
            web_url="https://sourcecraft.dev/team/platform-api",
        ),
        commit_sha="0123456789abcdef",
        analyzed_at=now,
        period_start=now,
        period_end=now,
    )


def _configure_mock_repo(mock_repo: MagicMock) -> None:
    """Настраивает фейковый клон: README есть; clone() делает temp_dir доступным."""
    mock_repo.temp_dir = None  # до clone рабочая область ещё не подготовлена
    mock_repo.cleanup.return_value = None
    mock_repo.file_exists.side_effect = lambda path: path in ("README.md", "LICENSE")
    mock_repo.read_file.return_value = "## How to run\n\n```bash\npytest\n```"
    mock_repo.read_file_safe.return_value = "# TODO: fix me\n"

    def do_clone():
        mock_repo.temp_dir = "/fake"
        return "/fake"

    mock_repo.clone.side_effect = do_clone


def _categories_by_code(execution):
    return {category.category: category for category in execution.analysis.categories}


@patch("backend.app.analysis.providers.LocalGitRepository")
def test_provider_registers_both_categories(mock_repo_cls, mock_context):
    """Провайдер отдаёт ровно documentation и code_health (1.1)."""
    registrations = tuple(repo_content_analyzer_provider(mock_context))

    assert sorted(registration.category for registration in registrations) == [
        "code_health",
        "documentation",
    ]


def test_project_life_provider_registers_activity_and_issues(mock_context):
    """Первый провайдер регистрирует Activity и Issues по тому же шаблону."""
    registrations = tuple(project_life_analyzer_provider(mock_context))

    assert sorted(registration.category for registration in registrations) == [
        "activity",
        "issues",
    ]


def test_project_life_provider_missing_token_yields_clear_error(mock_context):
    """Без токена категории API-провайдера возвращают ERROR с понятной причиной."""
    with patch("backend.app.analysis.providers.read_sourcecraft_token", return_value=""):
        registrations = tuple(project_life_analyzer_provider(mock_context))

    by_code = {registration.category: registration for registration in registrations}
    result = by_code["activity"].evaluate(mock_context)

    assert result.status == DataStatus.ERROR
    assert result.score is None
    assert "SOURCECRAFT_TOKEN" in (result.reason or "")


@patch("backend.app.analysis.providers.LocalGitRepository")
def test_orchestration_no_analyzer_not_configured_for_new_categories(
    mock_repo_cls, mock_context
):
    """В оркестрационном запуске новые категории не возвращают not_configured (1.1)."""
    mock_repo = MagicMock()
    _configure_mock_repo(mock_repo)
    mock_repo_cls.return_value = mock_repo

    with patch("backend.app.analysis.providers.os.walk") as mock_walk:
        mock_walk.return_value = [("/fake", [], ["main.py"])]
        # Бюджетный скан code_health проверяет размер файла через getsize до
        # чтения: мок пути /fake/main.py на диске не существует, поэтому размер
        # подменяется, иначе файл был бы пропущен (total_files == 0).
        with patch("os.path.getsize", return_value=16):
            registrations = tuple(repo_content_analyzer_provider(mock_context))
            execution = run_analysis(mock_context, registrations)

    categories = _categories_by_code(execution)
    assert categories["documentation"].status == DataStatus.MEASURED
    assert categories["code_health"].status == DataStatus.MEASURED
    assert categories["documentation"].reason != "analyzer_not_configured"
    assert categories["code_health"].reason != "analyzer_not_configured"


@patch("backend.app.analysis.providers.LocalGitRepository")
def test_provider_resolves_clone_url_and_injects_repository(mock_repo_cls, mock_context):
    """Коллекторы получают репозиторий извне, а не строят его по context (1.2, 2.1)."""
    mock_repo = MagicMock()
    mock_repo_cls.return_value = mock_repo

    with patch("backend.app.analysis.providers.read_sourcecraft_token", return_value=""):
        registrations = tuple(repo_content_analyzer_provider(mock_context))

    assert len(registrations) == 2
    # URL git-remota выведен из каталога SourceCraft (не web_url как таковой),
    # токен передан в LocalGitRepository, а не в AnalysisContext.
    mock_repo_cls.assert_called_once_with(
        repo_url="https://sourcecraft.dev/team/platform-api.git",
        ref=mock_context.commit_sha,
        auth_token=None,
    )


@patch("backend.app.analysis.providers.LocalGitRepository")
def test_shared_clone_used_by_both_categories(mock_repo_cls, mock_context):
    """Один clone на две категории: clone() вызывается один раз (1.5)."""
    mock_repo = MagicMock()
    _configure_mock_repo(mock_repo)
    mock_repo_cls.return_value = mock_repo

    with patch("backend.app.analysis.providers.os.walk") as mock_walk:
        mock_walk.return_value = []
        registrations = tuple(repo_content_analyzer_provider(mock_context))
        run_analysis(mock_context, registrations)

    mock_repo.clone.assert_called_once()
    assert mock_repo_cls.call_count == 1, "обе категории используют один LocalGitRepository"


@patch("backend.app.analysis.providers.LocalGitRepository")
def test_cleanup_called_after_success(mock_repo_cls, mock_context):
    """Cleanup вызывается после успешного запуска обеих категорий (T.7)."""
    mock_repo = MagicMock()
    _configure_mock_repo(mock_repo)
    mock_repo_cls.return_value = mock_repo

    with patch("backend.app.analysis.providers.os.walk") as mock_walk:
        mock_walk.return_value = []
        registrations = tuple(repo_content_analyzer_provider(mock_context))
        run_analysis(mock_context, registrations)

    mock_repo.cleanup.assert_called_once()


@patch("backend.app.analysis.providers.LocalGitRepository")
def test_cleanup_called_on_clone_error_path(mock_repo_cls, mock_context):
    """Cleanup вызывается и при ошибке клонирования (T.7)."""
    mock_repo = MagicMock()
    mock_repo.temp_dir = None
    mock_repo.clone.side_effect = GitCloneError("Не удалось получить содержимое репозитория.")
    mock_repo_cls.return_value = mock_repo

    registrations = tuple(repo_content_analyzer_provider(mock_context))
    execution = run_analysis(mock_context, registrations)

    categories = _categories_by_code(execution)
    assert categories["documentation"].status == DataStatus.ERROR
    assert categories["code_health"].status == DataStatus.ERROR
    mock_repo.clone.assert_called_once()
    mock_repo.cleanup.assert_called_once()


@patch("backend.app.analysis.providers.LocalGitRepository")
def test_git_failure_reason_never_leaks_url_or_token(mock_repo_cls, mock_context):
    """reason при git-ошибке не содержит URL и токен даже если сообщение утекло (T.3, 2.2)."""
    url = "https://secret-host.example/private/repo.git"
    token = "super-secret-token"

    mock_repo = MagicMock()
    mock_repo.temp_dir = None
    mock_repo.clone.side_effect = GitCloneError(f"boom {url} {token}")
    mock_repo.redact.side_effect = lambda text: (
        text.replace(url, "<repo>").replace(token, "<token>")
    )
    mock_repo_cls.return_value = mock_repo

    with patch.dict(os.environ, {"SOURCECRAFT_TOKEN": token}, clear=False):
        registrations = tuple(repo_content_analyzer_provider(mock_context))
    execution = run_analysis(mock_context, registrations)

    for category in _categories_by_code(execution).values():
        if category.category not in ("documentation", "code_health"):
            continue
        assert category.status == DataStatus.ERROR
        assert category.score is None
        reason = category.reason or ""
        assert url not in reason
        assert token not in reason


def test_clone_timeout_reason_never_leaks_url_or_token(mock_context):
    """TimeoutExpired (I.1) хранит в reason только чистый текст без URL/токена (V.4).

    Путь конца-в-конец: реальный LocalGitRepository, subprocess.run падает с
    TimeoutExpired, чей cmd несёт URL репозитория; clone() оборачивает его в
    GitCloneError со статическим текстом, а провайдер дополнительно прогоняет
    текст через repository.redact() перед сохранением в CategoryResult.reason.
    """
    url = "https://secret-host.example/private/repo.git"
    token = "super-secret-token"

    with patch.dict(os.environ, {"SOURCECRAFT_TOKEN": token}, clear=False), patch(
        "backend.app.integrations.git_repository.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["git", url], timeout=60),
    ):
        registrations = tuple(repo_content_analyzer_provider(mock_context))
        execution = run_analysis(mock_context, registrations)

    for category in _categories_by_code(execution).values():
        if category.category not in ("documentation", "code_health"):
            continue
        assert category.status == DataStatus.ERROR
        assert category.score is None
        reason = category.reason or ""
        assert url not in reason
        assert token not in reason
        assert "ожидания" in reason


def test_programming_error_reason_is_static_and_clean(mock_context):
    """Внутренняя ошибка анализатора (I.6) не кладёт текст исключения в reason (V.4).

    Ветка «programming error» возвращает фиксированную строку
    ``analyzer_execution_failed``, а не str(exception), поэтому даже исключение с
    URL и токеном в сообщении не может протечь в CategoryResult.reason.
    """
    url = "https://secret-host.example/private/repo.git"
    token = "super-secret-token"

    with patch.dict(os.environ, {"SOURCECRAFT_TOKEN": token}, clear=False), patch(
        "backend.app.analysis.providers.LocalGitRepository"
    ) as mock_repo_cls:
        mock_repo = MagicMock()
        mock_repo.temp_dir = "/fake"
        mock_repo_cls.return_value = mock_repo
        with patch(
            "backend.app.analyzers.documentation.collect",
            side_effect=RuntimeError(f"programming bug {url} {token}"),
        ):
            registrations = tuple(repo_content_analyzer_provider(mock_context))
            execution = run_analysis(mock_context, registrations)

    categories = _categories_by_code(execution)
    assert categories["documentation"].status == DataStatus.ERROR
    assert categories["documentation"].score is None
    reason = categories["documentation"].reason or ""
    assert reason == "analyzer_execution_failed"
    assert url not in reason
    assert token not in reason