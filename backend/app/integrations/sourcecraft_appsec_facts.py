"""Адаптация безопасной AppSec-сводки SourceCraft к фактам Security."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from backend.app.analyzers.security import SecurityFacts, build_facts
from backend.app.contracts import RepositoryRef
from backend.app.integrations.sourcecraft_appsec_probe import (
    APPSEC_ENGINES,
    AppSecProbeResult,
)

_PROBE_FAILURE = "sourcecraft_appsec_probe_failed"
_MAPPING_FAILURE = "sourcecraft_appsec_mapping_failed"


class AppSecProbe(Protocol):
    """Минимальный контракт безопасного зонда AppSec."""

    def probe_all(self, repository: str) -> tuple[AppSecProbeResult, ...]:
        """Возвращает только агрегаты SAST, SCA и secret scanning."""


def collect_security_facts(
    probe: AppSecProbe,
    repository: RepositoryRef,
) -> SecurityFacts:
    """Преобразует три сводки AppSec в честный вход Security-анализатора.

    В ``payload`` попадают только тип движка, доступность, размер
    ограниченной выборки, уровни критичности и фиксированная причина.
    Сырые findings, пути, правила, фрагменты кода и секреты сюда не передаются.
    """

    repository_slug = f"{repository.organization_slug}/{repository.repository_slug}"
    try:
        results = tuple(probe.probe_all(repository_slug))
    except Exception:  # noqa: BLE001 — текст ошибки зонда может содержать private data.
        return build_facts(None, source_error=_PROBE_FAILURE)

    return build_security_facts_from_results(results)


def build_security_facts_from_results(
    results: tuple[object, ...],
) -> SecurityFacts:
    """Строит ``SecurityFacts`` из уже безопасных результатов трёх движков.

    Этим швом пользуются и CLI-зонд, и безопасный файловый bridge. Он принимает
    только ``AppSecProbeResult``: сырые ответы SourceCraft не могут случайно
    попасть в анализатор через альтернативный источник данных.
    """

    if not _has_complete_engine_set(results):
        return build_facts(None, source_error=_MAPPING_FAILURE)

    ordered_results = tuple(
        next(result for result in results if result.engine == engine)
        for engine in APPSEC_ENGINES
    )
    if any(result.availability == "available" for result in ordered_results):
        return build_facts({"engines": [result.as_dict() for result in ordered_results]})
    if all(result.availability == "unavailable" for result in ordered_results):
        return build_facts(None)
    return build_facts(None, source_error=_PROBE_FAILURE)


def make_security_facts_provider(
    probe: AppSecProbe,
) -> Callable[[RepositoryRef], SecurityFacts]:
    """Создаёт поставщик, совместимый с ``security.make_analyzer``."""

    def provide(repository: RepositoryRef) -> SecurityFacts:
        return collect_security_facts(probe, repository)

    return provide


def _has_complete_engine_set(results: tuple[object, ...]) -> bool:
    if len(results) != len(APPSEC_ENGINES):
        return False
    if not all(isinstance(result, AppSecProbeResult) for result in results):
        return False
    return {result.engine for result in results} == set(APPSEC_ENGINES)
