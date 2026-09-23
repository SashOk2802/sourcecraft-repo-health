"""Подключение Activity и Issues к общему запуску анализа.

Ядро и диспетчер видят анализатор как функцию от AnalysisContext. Токена в контексте
нет, поэтому клиент сюда не передаётся заранее и не живёт на весь процесс.

Провайдер соответствует хуку диспетчера: его вызывают в submit, а collect идёт
позже, уже в рабочем потоке. Клиент открывает фабрика внутри этого вызова и
закрывается до возврата. Токена в AnalysisContext нет, поэтому фабрика берёт
его снаружи — из сборки приложения. Общий токен процесса в модуль не зашит.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from backend.app.analysis.dispatch import AnalyzerProvider
from backend.app.analysis.runner import AnalyzerRegistration
from backend.app.analyzers.activity import CATEGORY_CODE as ACTIVITY_CODE
from backend.app.analyzers.activity import collect as collect_activity
from backend.app.analyzers.activity import evaluate as evaluate_activity
from backend.app.analyzers.issues import CATEGORY_CODE as ISSUES_CODE
from backend.app.analyzers.issues import collect as collect_issues
from backend.app.analyzers.issues import evaluate as evaluate_issues
from backend.app.contracts import AnalysisContext, CategoryResult
from backend.app.integrations.sourcecraft import SourceCraftClient

# Фабрика обязана вернуть новый клиент на каждый вызов. Модуль закроет его сам.
# Нельзя отдавать клиент из внешнего with: к моменту collect соединение уже мёртвое.
# SourceCraftClient.close() отпускает пул только если создал его сам. Фабрика,
# которая передаёт свой httpx.Client, должна закрывать этот пул сама.
ClientFactory = Callable[[AnalysisContext], SourceCraftClient]


def project_life_analyzer_provider(open_client: ClientFactory) -> AnalyzerProvider:
    """Регистрации Activity и Issues для InProcessAnalysisDispatcher.

    Бюджет страниц остаётся DEFAULT_MAX_PAGES анализаторов, пока запуск не
    получит собственную настройку. Остальные категории не объявляются: ядро
    помечает их как analyzer_not_configured.
    """

    def provide(context: AnalysisContext) -> tuple[AnalyzerRegistration, ...]:
        # Здесь контекст не нужен: фабрика получит его в evaluate, уже в потоке collect.
        # Токена в AnalysisContext нет, выбирать токен пользователя на этом шве нельзя.
        del context
        return (
            AnalyzerRegistration(ACTIVITY_CODE, partial(_run_activity, open_client)),
            AnalyzerRegistration(ISSUES_CODE, partial(_run_issues, open_client)),
        )

    return provide


def _run_activity(open_client: ClientFactory, context: AnalysisContext) -> CategoryResult:
    """Открывает клиент на время сбора Activity и закрывает его до возврата."""
    client = open_client(context)
    try:
        return evaluate_activity(collect_activity(client, context.repository), context)
    finally:
        client.close()


def _run_issues(open_client: ClientFactory, context: AnalysisContext) -> CategoryResult:
    """Открывает клиент на время сбора Issues и закрывает его до возврата."""
    client = open_client(context)
    try:
        return evaluate_issues(collect_issues(client, context.repository), context)
    finally:
        client.close()
