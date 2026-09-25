from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

import httpx

from backend.app.analysis import AnalysisPrincipal, AnalyzerRegistration, InMemoryAnalysisJobStore
from backend.app.analysis.providers import sourcecraft_analyzer_provider
from backend.app.analysis.store import InMemoryAnalysisStore
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef
from backend.app.integrations.sourcecraft import (
    SourceCraftClient,
    SourceCraftNetworkError,
    SourceCraftRequestError,
)
from backend.app.integrations.sourcecraft_repository import (
    SourceCraftPublicCatalogSettings,
    SourceCraftPublicRepositoryResolver,
    SourceCraftRepositoryUnavailableError,
    create_sourcecraft_public_repository_resolver_from_environment,
)
from backend.app.main import create_app


class SourceCraftPublicRepositoryResolverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)
        self.principal = AnalysisPrincipal("user-42")

    async def test_resolves_only_public_repository_from_configured_catalog(self) -> None:
        client = CatalogClient([repository_payload()])
        resolver = SourceCraftPublicRepositoryResolver(
            SourceCraftPublicCatalogSettings("token", ("team",)),
            client_factory=lambda _: client,
            clock=lambda: self.now,
        )

        context = await resolver.resolve("repo-42", self.principal)

        self.assertEqual(context.repository.id, "repo-42")
        self.assertEqual(context.repository.organization_slug, "team")
        self.assertEqual(context.repository.repository_slug, "platform-api")
        self.assertEqual(context.commit_sha, "main")
        self.assertEqual(context.period_end, self.now)
        self.assertTrue(client.closed)
        self.assertEqual(client.paths, ["/orgs/team/repos"])

    async def test_rejects_private_repository_even_when_catalog_token_can_read_it(self) -> None:
        client = CatalogClient([repository_payload(visibility="private")])
        resolver = SourceCraftPublicRepositoryResolver(
            SourceCraftPublicCatalogSettings("token", ("team",)),
            client_factory=lambda _: client,
            clock=lambda: self.now,
        )

        with self.assertRaises(PermissionError):
            await resolver.resolve("repo-42", self.principal)

        self.assertTrue(client.closed)

    async def test_rejects_catalog_url_outside_sourcecraft_before_git_uses_token(self) -> None:
        payload = repository_payload()
        payload["web_url"] = "https://attacker.example/team/platform-api"
        resolver = SourceCraftPublicRepositoryResolver(
            SourceCraftPublicCatalogSettings("token", ("team",)),
            client_factory=lambda _: CatalogClient([payload]),
            clock=lambda: self.now,
        )

        with self.assertRaises(SourceCraftRepositoryUnavailableError):
            await resolver.resolve("repo-42", self.principal)

    async def test_distinguishes_unknown_repository_and_catalog_failure(self) -> None:
        unknown = SourceCraftPublicRepositoryResolver(
            SourceCraftPublicCatalogSettings("token", ("team",)),
            client_factory=lambda _: CatalogClient([]),
            clock=lambda: self.now,
        )
        unavailable = SourceCraftPublicRepositoryResolver(
            SourceCraftPublicCatalogSettings("token", ("team",)),
            client_factory=lambda _: FailingCatalogClient(),
            clock=lambda: self.now,
        )

        with self.assertRaises(LookupError):
            await unknown.resolve("unknown", self.principal)
        with self.assertRaises(SourceCraftRepositoryUnavailableError):
            await unavailable.resolve("repo-42", self.principal)

    def test_clone_url_uses_only_allowlisted_sourcecraft_hosts(self) -> None:
        self.assertEqual(
            SourceCraftClient.resolve_git_clone_url(
                "team",
                "platform-api",
                "https://sourcecraft.dev/team/platform-api",
            ),
            "https://sourcecraft.dev/team/platform-api.git",
        )
        for web_url in (
            "https://attacker.example/team/platform-api",
            "https://sourcecraft.dev:444/team/platform-api",
        ):
            with self.subTest(web_url=web_url), self.assertRaises(SourceCraftRequestError):
                SourceCraftClient.resolve_git_clone_url("team", "platform-api", web_url)

    def test_environment_requires_token_and_public_organizations_together(self) -> None:
        self.assertIsNone(create_sourcecraft_public_repository_resolver_from_environment({}))
        with self.assertRaisesRegex(ValueError, "configured together"):
            create_sourcecraft_public_repository_resolver_from_environment(
                {"SOURCECRAFT_TOKEN": "token"}
            )
        with self.assertRaisesRegex(ValueError, "configured together"):
            create_sourcecraft_public_repository_resolver_from_environment(
                {"SOURCECRAFT_PUBLIC_ORGANIZATIONS": "team"}
            )


class ProductionAnalysisWiringTest(unittest.IsolatedAsyncioTestCase):
    async def test_configured_production_app_accepts_analysis_request(self) -> None:
        context = analysis_context()
        resolver = StaticResolver(context)
        store = InMemoryAnalysisStore()
        jobs = InMemoryAnalysisJobStore()
        registrations = tuple(
            AnalyzerRegistration(category, measured_evaluator(category))
            for category in (
                "activity",
                "issues",
                "cicd",
                "documentation",
                "code_health",
                "security",
            )
        )

        with (
            patch(
                "backend.app.main._default_analysis_store",
                return_value=store,
            ),
            patch(
                "backend.app.main._default_analysis_job_store",
                return_value=jobs,
            ),
            patch(
                "backend.app.main.create_sourcecraft_public_repository_resolver_from_environment",
                return_value=resolver,
            ),
            patch(
                "backend.app.main.sourcecraft_analyzer_provider",
                return_value=registrations,
            ),
        ):
            app = create_app(principal_provider=authenticated_principal)

        dispatcher = app.state.analysis_dispatcher
        self.assertIsNotNone(dispatcher)

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                response = await client.post("/api/v1/repositories/repo-42/analyses")
        finally:
            await dispatcher.close()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(resolver.calls, [("repo-42", AnalysisPrincipal("user-42"))])

    def test_sourcecraft_provider_registers_every_category(self) -> None:
        registrations = tuple(sourcecraft_analyzer_provider(analysis_context()))

        self.assertEqual(
            {registration.category for registration in registrations},
            {"activity", "issues", "cicd", "documentation", "code_health", "security"},
        )


class CatalogClient:
    def __init__(self, repositories: list[dict[str, object]]) -> None:
        self._repositories = repositories
        self.paths: list[str] = []
        self.closed = False

    def get_paginated_objects(
        self,
        path: str,
        *,
        items_field: str,
        page_size: int = 100,
        max_pages: int = 100,
    ) -> list[dict[str, object]]:
        self.paths.append(path)
        if items_field != "repositories" or page_size != 100:
            raise AssertionError("unexpected SourceCraft catalog request")
        return self._repositories

    def close(self) -> None:
        self.closed = True


class FailingCatalogClient:
    def get_paginated_objects(
        self,
        path: str,
        *,
        items_field: str,
        page_size: int = 100,
        max_pages: int = 100,
    ) -> list[dict[str, object]]:
        raise SourceCraftNetworkError("network failure")

    def close(self) -> None:
        pass


class StaticResolver:
    def __init__(self, context: AnalysisContext) -> None:
        self._context = context
        self.calls: list[tuple[str, AnalysisPrincipal]] = []

    async def resolve(
        self,
        repository_id: str,
        principal: AnalysisPrincipal,
    ) -> AnalysisContext:
        self.calls.append((repository_id, principal))
        return self._context


async def authenticated_principal(_: httpx.Request) -> AnalysisPrincipal:
    return AnalysisPrincipal("user-42")


def analysis_context() -> AnalysisContext:
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    return AnalysisContext(
        repository=RepositoryRef(
            id="repo-42",
            organization_slug="team",
            repository_slug="platform-api",
            web_url="https://sourcecraft.dev/team/platform-api",
        ),
        commit_sha="main",
        analyzed_at=now,
        period_start=now,
        period_end=now,
    )


def measured_evaluator(category: str):
    def evaluate(_: AnalysisContext) -> CategoryResult:
        return CategoryResult(
            category=category,
            status=DataStatus.MEASURED,
            score=80,
            summary=f"Категория {category} измерена.",
        )

    return evaluate


def repository_payload(*, visibility: str = "public") -> dict[str, object]:
    return {
        "id": "repo-42",
        "slug": "platform-api",
        "default_branch": "main",
        "visibility": visibility,
        "organization": {"slug": "team"},
        "web_url": "https://sourcecraft.dev/team/platform-api",
    }
