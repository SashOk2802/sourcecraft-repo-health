"""Регистрация анализаторов категорий для запуска анализа.

Провайдеры связывают `collect`/`evaluate` анализаторов с источником данных и
жизненным циклом ресурсов: HTTP-клиент SourceCraft закрывается после категории,
временный git-клон создаётся один раз на запуск и удаляется после последней
категории, использующей его (требования 1.1–1.3, I.5, I.6).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from backend.app.analysis.runner import AnalyzerRegistration
from backend.app.analyzers import activity, cicd, code_health, documentation, issues, security
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus
from backend.app.integrations.git_repository import GitCloneError, LocalGitRepository
from backend.app.integrations.sourcecraft import SourceCraftClient, SourceCraftClientError
from backend.app.integrations.sourcecraft_appsec_snapshot import (
    SourceCraftAppSecSnapshotStore,
    snapshot_settings_from_environment,
)
from backend.app.integrations.sourcecraft_cicd import SourceCraftCicdClient
from backend.app.integrations.sourcecraft_cicd_facts import make_cicd_facts_provider

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

_TOKEN_ENV_VAR = "SOURCECRAFT_TOKEN"

_TOKEN_MISSING_REASON = f"Отсутствует токен SourceCraft ({_TOKEN_ENV_VAR})."


def read_sourcecraft_token() -> str:
    """Читает токен из окружения или локального .env (без логирования)."""
    token = os.environ.get(_TOKEN_ENV_VAR, "").strip()
    if token:
        return token
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith(f"{_TOKEN_ENV_VAR}="):
                return line.partition("=")[2].strip().strip('"').strip("'")
    return ""


def project_life_analyzer_provider(context: AnalysisContext) -> Iterable[AnalyzerRegistration]:
    """Регистрирует категории Activity и Issues (данные из REST API SourceCraft).

    Каждая категория получает собственный клиент на время своего запуска;
    клиент закрывается в ``finally`` до возврата результата.
    """
    token = read_sourcecraft_token()
    return (
        AnalyzerRegistration("activity", _activity_evaluator(token)),
        AnalyzerRegistration("issues", _client_evaluator("issues", token, issues)),
    )


def repo_content_analyzer_provider(context: AnalysisContext) -> Iterable[AnalyzerRegistration]:
    """Регистрирует категории Documentation и Code health (анализ файлов репозитория).

    Git-remot URL резолвится через ``SourceCraftClient.resolve_git_clone_url`` —
    тот же источник каталога и тот же Bearer-PAT, что у API-клиента. Один
    аутентифицированный ``LocalGitRepository`` передаётся обоим коллекторам:
    clone выполняется один раз, cleanup — после возврата результата последней
    категории, поэтому временная директория не переживает запуск.
    """
    token = read_sourcecraft_token()
    repo_url = SourceCraftClient.resolve_git_clone_url(
        context.repository.organization_slug,
        context.repository.repository_slug,
        context.repository.web_url,
    )
    repository = LocalGitRepository(
        repo_url=repo_url,
        ref=context.commit_sha,
        auth_token=token or None,
    )
    workspace = _SharedCloneWorkspace(repository)
    return (
        AnalyzerRegistration(
            "documentation",
            _workspace_evaluator("documentation", documentation, workspace),
        ),
        AnalyzerRegistration(
            "code_health",
            _workspace_evaluator("code_health", code_health, workspace),
        ),
    )


def sourcecraft_analyzer_provider(context: AnalysisContext) -> Iterable[AnalyzerRegistration]:
    """Регистрирует все шесть категорий для одного production-запуска.

    CI/CD использует подтверждённый REST-адаптер. Security читает только
    опциональный свежий AppSec snapshot из read-only каталога. Его создаёт
    локальный CLI-процесс пользователя; worker никогда не получает IAM-сессию
    или raw finding'и. Без snapshot'а Security честно остаётся unavailable.
    """
    token = read_sourcecraft_token()
    return (
        *project_life_analyzer_provider(context),
        AnalyzerRegistration("cicd", _cicd_evaluator(token)),
        *repo_content_analyzer_provider(context),
        AnalyzerRegistration("security", _security_evaluator()),
    )


def _activity_evaluator(token: str):
    """Возвращает Activity evaluator с API-фактами и отдельной историей Git."""

    def evaluate(ctx: AnalysisContext) -> CategoryResult:
        try:
            client = SourceCraftClient(token)
        except ValueError:
            return _sourcecraft_configuration_error("activity")
        try:
            facts = activity.collect(client, ctx.repository)
            history = activity.collect_commit_history(ctx, auth_token=token or None)
            return activity.evaluate(replace(facts, commit_history=history), ctx)
        except SourceCraftClientError as error:
            return CategoryResult(
                category="activity",
                status=DataStatus.ERROR,
                score=None,
                summary="Не удалось получить данные из SourceCraft.",
                reason=str(error),
            )
        finally:
            client.close()

    return evaluate


def _cicd_evaluator(token: str):
    """Возвращает CI/CD evaluator с отдельным закрываемым REST-клиентом."""

    def evaluate(ctx: AnalysisContext) -> CategoryResult:
        try:
            client = SourceCraftClient(token)
        except ValueError:
            return _sourcecraft_configuration_error("cicd")
        try:
            facts_provider = make_cicd_facts_provider(SourceCraftCicdClient(client))
            return cicd.make_analyzer(facts_provider)(ctx)
        finally:
            client.close()

    return evaluate


def _security_evaluator():
    """Регистрирует Security с опциональным безопасным AppSec snapshot-ом."""

    settings = snapshot_settings_from_environment()
    if settings is None:
        return security.make_analyzer(lambda _: security.build_facts(None))
    store = SourceCraftAppSecSnapshotStore(settings)
    return security.make_context_analyzer(store.collect)


def _sourcecraft_configuration_error(category_code: str) -> CategoryResult:
    return CategoryResult(
        category=category_code,
        status=DataStatus.ERROR,
        score=None,
        summary="Не удалось получить данные из SourceCraft.",
        reason=_TOKEN_MISSING_REASON,
    )


def _client_evaluator(
    category_code: str,
    token: str,
    module: object,
):
    """Возвращает evaluate, создающий клиент SourceCraft на время одной категории."""

    def evaluate(ctx: AnalysisContext) -> CategoryResult:
        try:
            client = SourceCraftClient(token)
        except ValueError:
            return CategoryResult(
                category=category_code,
                status=DataStatus.ERROR,
                score=None,
                summary="Не удалось получить данные из SourceCraft.",
                reason=_TOKEN_MISSING_REASON,
            )
        try:
            facts = module.collect(client, ctx.repository)  # type: ignore[attr-defined]
            return module.evaluate(facts, ctx)  # type: ignore[attr-defined]
        except SourceCraftClientError as error:
            # Интеграционные ошибки отделены от внутренних багов (I.6);
            # тексты ошибок SourceCraftClient уже не содержат токен.
            return CategoryResult(
                category=category_code,
                status=DataStatus.ERROR,
                score=None,
                summary="Не удалось получить данные из SourceCraft.",
                reason=str(error),
            )
        finally:
            client.close()

    return evaluate


def _workspace_evaluator(
    category_code: str,
    module: object,
    workspace: _SharedCloneWorkspace,
):
    """Возвращает evaluate, работающий с общим git-клоном рабочей области."""

    def evaluate(ctx: AnalysisContext) -> CategoryResult:
        try:
            repository = workspace.acquire()
            facts = module.collect(repository)  # type: ignore[attr-defined]
            return module.evaluate(ctx, facts)  # type: ignore[attr-defined]
        except GitCloneError as error:
            # Ошибка git/clone отделена от внутренних ошибок (I.6); текст
            # исключения GitCloneError по контракту чист, но на всякий случай
            # URL и токен вырезаются ещё раз перед сохранением (2.2).
            return CategoryResult(
                category=category_code,
                status=DataStatus.ERROR,
                score=None,
                summary="Не удалось получить содержимое репозитория.",
                reason=workspace.repository.redact(str(error)),
            )
        except Exception:
            logger.exception("Внутренняя ошибка анализатора %s.", category_code)
            return CategoryResult(
                category=category_code,
                status=DataStatus.ERROR,
                score=None,
                summary="Не удалось рассчитать категорию.",
                reason="analyzer_execution_failed",
            )
        finally:
            workspace.release()

    return evaluate


class _SharedCloneWorkspace:
    """Один git-клон на несколько коллекторов; уборка после последнего.

    Гарантирует, что документация и code_health не клонируют один репозиторий
    независимо (I.5) и что временная директория не переживёт запуск (1.3):
    cleanup вызывается после возврата результата последней категории, а ошибка
    cleanup не пробрасывается наружу (I.3 — cleanup сам по себе безопасен).
    """

    def __init__(self, repository: LocalGitRepository, users: int = 2) -> None:
        self._repository = repository
        self._remaining_users = users
        self._clone_error: Exception | None = None

    @property
    def repository(self) -> LocalGitRepository:
        return self._repository

    def acquire(self) -> LocalGitRepository:
        """Клонирует репозиторий при первом использовании и возвращает его."""
        if self._clone_error is not None:
            raise self._clone_error
        if self._repository.temp_dir is None:
            try:
                self._repository.clone()
            except Exception as error:
                self._clone_error = error
                raise
        return self._repository

    def release(self) -> None:
        """Освобождает использование; после последнего — удаляет временную папку."""
        self._remaining_users -= 1
        if self._remaining_users <= 0:
            self._repository.cleanup()
