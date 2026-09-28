"""Адаптация запусков SourceCraft к безопасным фактам CI/CD-анализатора."""

from __future__ import annotations

from collections.abc import Callable

from backend.app.analyzers.cicd import CicdFacts, CiRunFact, build_facts
from backend.app.contracts import RepositoryRef
from backend.app.integrations.sourcecraft import SourceCraftClientError
from backend.app.integrations.sourcecraft_cicd import SourceCraftCicdClient, SourceCraftCiRun

_CLIENT_FAILURE = "sourcecraft_cicd_client_failed"
_MAPPING_FAILURE = "sourcecraft_cicd_mapping_failed"


def collect_cicd_facts(
    client: SourceCraftCicdClient,
    repository: RepositoryRef,
) -> CicdFacts:
    """Читает все доступные CI-запуски и оставляет только нужные для Score поля.

    Сетевые и API-ошибки превращаются в безопасный маркер, без текста ответа,
    токена или сведений закрытого репозитория. Пустой API ``id`` не используется:
    стабильность фактов обеспечивается обязательным ``slug`` запуска.
    """

    try:
        runs = client.list_runs(repository)
    except SourceCraftClientError:
        return build_facts(None, source_error=_CLIENT_FAILURE)

    try:
        return build_facts(
            CiRunFact(
                slug=run.slug,
                status=run.status,
                event_type=run.event_type,
                created_at=run.created_at,
                duration_seconds=_duration_seconds(run),
            )
            for run in runs
        )
    except ValueError:
        # Контракт клиента и контракты фактов могут разойтись при изменении API.
        # Нельзя выдавать частичный список за полную историю запусков.
        return build_facts(None, source_error=_MAPPING_FAILURE)


def make_cicd_facts_provider(
    client: SourceCraftCicdClient,
) -> Callable[[RepositoryRef], CicdFacts]:
    """Создаёт поставщик фактов, совместимый с ``make_analyzer`` категории CI/CD."""

    def provide(repository: RepositoryRef) -> CicdFacts:
        return collect_cicd_facts(client, repository)

    return provide


def _duration_seconds(run: SourceCraftCiRun) -> float | None:
    started_at = run.started_at
    finished_at = run.finished_at
    if started_at is None or finished_at is None:
        return None
    duration = (finished_at - started_at).total_seconds()
    if duration < 0:
        raise ValueError("CI run duration must not be negative")
    return duration
