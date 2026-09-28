"""Server-side выбор personal/public SourceCraft анализа без передачи PAT браузеру."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from backend.app.analysis.dispatch import (
    AnalysisPlan,
    AnalysisPrincipal,
    AnalyzerProvider,
    RepositoryContextResolver,
)
from backend.app.analysis.providers import personal_sourcecraft_analyzer_provider
from backend.app.identity.sourcecraft_connection import SourceCraftConnectionService
from backend.app.launch import SourceCraftRepositoryContextResolver


class SourceCraftConnectionRequiredError(RuntimeError):
    """Репозиторий не разрешён public-каталогом, а personal connection отсутствует."""


class PersonalOrPublicAnalysisPlanner:
    """Выбирает режим по server-side connection текущего principal."""

    def __init__(
        self,
        *,
        connection_service: SourceCraftConnectionService | None,
        public_resolver: RepositoryContextResolver | None,
        public_analyzer_provider: AnalyzerProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._connections = connection_service
        self._public_resolver = public_resolver
        self._public_analyzer_provider = public_analyzer_provider
        self._clock = clock

    async def plan(
        self,
        repository_id: str,
        principal: AnalysisPrincipal,
    ) -> AnalysisPlan:
        lease = (
            await self._connections.issue_lease(principal.subject)
            if self._connections is not None
            else None
        )
        if lease is not None:
            owner_subject = principal.subject

            def open_client():
                assert self._connections is not None
                return self._connections.open_client(lease, owner_subject)

            resolver = SourceCraftRepositoryContextResolver(
                open_client,
                clock=self._clock,
                principal_guard=lambda actual: _require_subject(actual, owner_subject),
            )
            context = await resolver.resolve(repository_id, principal)
            return AnalysisPlan(
                context,
                tuple(personal_sourcecraft_analyzer_provider(context, open_client)),
            )

        if self._public_resolver is None:
            raise SourceCraftConnectionRequiredError
        try:
            context = await self._public_resolver.resolve(repository_id, principal)
        except LookupError:
            raise SourceCraftConnectionRequiredError from None
        return AnalysisPlan(context, tuple(self._public_analyzer_provider(context)))


def _require_subject(principal: AnalysisPrincipal, expected_subject: str) -> None:
    if principal.subject != expected_subject:
        raise PermissionError("SourceCraft credential lease belongs to another user")
