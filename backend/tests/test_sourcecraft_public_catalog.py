"""Проверяет выдачу только public-репозиториев SourceCraft без сети."""

from __future__ import annotations

import unittest

from backend.app.integrations.sourcecraft import SourceCraftTimeoutError
from backend.app.integrations.sourcecraft_public_catalog import (
    SourceCraftPublicRepositoryCatalog,
    create_sourcecraft_public_repository_catalog_from_environment,
)
from backend.app.integrations.sourcecraft_repository import (
    SourceCraftPublicCatalogSettings,
    SourceCraftRepositoryUnavailableError,
)


class SourceCraftPublicRepositoryCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_only_public_repositories_in_stable_order_and_closes_client(self) -> None:
        clients: list[CatalogHttpClient] = []

        def create_client(token: str) -> CatalogHttpClient:
            client = CatalogHttpClient(
                token,
                {
                    "alpha": [
                        repository_payload("private-id", "private", visibility="private"),
                        repository_payload("zeta-id", "zeta", organization_slug="alpha"),
                    ],
                    "beta": [
                        repository_payload("repository-two", "core", organization_slug="beta"),
                        repository_payload(
                            "internal-id",
                            "internal",
                            organization_slug="beta",
                            visibility="internal",
                        ),
                    ],
                },
            )
            clients.append(client)
            return client

        catalog = SourceCraftPublicRepositoryCatalog(
            SourceCraftPublicCatalogSettings("sourcecraft-secret", ("beta", "alpha")),
            client_factory=create_client,
        )

        repositories = await catalog.list_repositories()

        self.assertEqual(
            [(repository.organization_slug, repository.slug) for repository in repositories],
            [("alpha", "zeta"), ("beta", "core")],
        )
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].token, "sourcecraft-secret")
        self.assertTrue(clients[0].closed)
        self.assertEqual(
            clients[0].requests,
            ["/orgs/beta/repos", "/orgs/alpha/repos"],
        )

    async def test_hides_sourcecraft_error_and_closes_client(self) -> None:
        client = CatalogHttpClient(
            "sourcecraft-secret", {}, error=SourceCraftTimeoutError("timeout")
        )
        catalog = SourceCraftPublicRepositoryCatalog(
            SourceCraftPublicCatalogSettings("sourcecraft-secret", ("team",)),
            client_factory=lambda _: client,
        )

        with self.assertRaisesRegex(
            SourceCraftRepositoryUnavailableError,
            "SourceCraft repository catalog is unavailable",
        ) as raised:
            await catalog.list_repositories()

        self.assertTrue(client.closed)
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("sourcecraft-secret", str(raised.exception))

    async def test_rejects_duplicate_public_repository_across_organizations(self) -> None:
        client = CatalogHttpClient(
            "sourcecraft-secret",
            {
                "alpha": [repository_payload("same-id", "first", organization_slug="alpha")],
                "beta": [repository_payload("same-id", "second", organization_slug="beta")],
            },
        )
        catalog = SourceCraftPublicRepositoryCatalog(
            SourceCraftPublicCatalogSettings("sourcecraft-secret", ("alpha", "beta")),
            client_factory=lambda _: client,
        )

        with self.assertRaisesRegex(SourceCraftRepositoryUnavailableError, "duplicate public"):
            await catalog.list_repositories()

        self.assertTrue(client.closed)

    async def test_global_discovery_returns_public_repositories_across_organizations(self) -> None:
        client = CatalogHttpClient(
            "sourcecraft-secret",
            {},
            discovered_repositories=[
                repository_payload("repo-b", "second", organization_slug="beta"),
                repository_payload("repo-a", "first", organization_slug="alpha"),
            ],
        )
        catalog = SourceCraftPublicRepositoryCatalog(
            SourceCraftPublicCatalogSettings(
                "sourcecraft-secret", discover_all_public=True
            ),
            client_factory=lambda _: client,
        )

        repositories = await catalog.list_repositories()

        self.assertEqual(
            [(repository.organization_slug, repository.slug) for repository in repositories],
            [("alpha", "first"), ("beta", "second")],
        )
        self.assertEqual(client.requests, ["/repos"])
        self.assertEqual(client.request_params, [{"sort_by": "created_at"}])
        self.assertTrue(client.closed)


class PublicCatalogDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    """Публичный репозиторий вне разрешённых организаций добавляется по запросу публичного API."""

    def setUp(self) -> None:
        self.organizations: dict[str, list[dict[str, object]]] = {
            "alpha": [repository_payload("alpha-id", "core")],
            "other": [
                repository_payload("open-id", "open", organization_slug="other"),
                repository_payload("closed-id", "closed", organization_slug="other", visibility="private"),
            ],
        }
        self.catalog = SourceCraftPublicRepositoryCatalog(
            SourceCraftPublicCatalogSettings("sourcecraft-secret", ("alpha",)),
            client_factory=lambda token: CatalogHttpClient(token, self.organizations),
        )

    async def test_discovered_public_repository_joins_catalog(self) -> None:
        discovered = await self.catalog.discover("other", "open")

        self.assertIsNotNone(discovered)
        slugs = [(item.organization_slug, item.slug) for item in await self.catalog.list_repositories()]
        self.assertEqual(slugs, [("alpha", "core"), ("other", "open")])

    async def test_private_and_unknown_repositories_are_not_added(self) -> None:
        self.assertIsNone(await self.catalog.discover("other", "closed"))
        self.assertIsNone(await self.catalog.discover("other", "missing"))

        slugs = [(item.organization_slug, item.slug) for item in await self.catalog.list_repositories()]
        self.assertEqual(slugs, [("alpha", "core")])

    async def test_repository_that_became_private_leaves_catalog(self) -> None:
        await self.catalog.discover("other", "open")
        self.organizations["other"] = [
            repository_payload("open-id", "open", organization_slug="other", visibility="private"),
        ]

        slugs = [(item.organization_slug, item.slug) for item in await self.catalog.list_repositories()]
        self.assertEqual(slugs, [("alpha", "core")])


class SourceCraftPublicCatalogSettingsTest(unittest.TestCase):
    def test_factory_requires_complete_configuration_and_hides_token_from_repr(self) -> None:
        self.assertIsNone(create_sourcecraft_public_repository_catalog_from_environment({}))
        self.assertIsNone(
            create_sourcecraft_public_repository_catalog_from_environment(
                {"SOURCECRAFT_TOKEN": "secret"}
            )
        )
        with self.assertRaisesRegex(ValueError, "SOURCECRAFT_TOKEN"):
            create_sourcecraft_public_repository_catalog_from_environment(
                {"SOURCECRAFT_PUBLIC_ORGANIZATIONS": "team"}
            )

        settings = SourceCraftPublicCatalogSettings("sourcecraft-secret", ("team",))

        self.assertNotIn("sourcecraft-secret", repr(settings))

    def test_factory_supports_explicit_global_discovery_only(self) -> None:
        catalog = create_sourcecraft_public_repository_catalog_from_environment(
            {
                "SOURCECRAFT_TOKEN": "sourcecraft-secret",
                "SOURCECRAFT_DISCOVER_PUBLIC_REPOSITORIES": "true",
            }
        )

        self.assertIsNotNone(catalog)
        self.assertTrue(catalog._settings.discover_all_public)
        self.assertEqual(catalog._settings.organization_slugs, ())
        self.assertNotIn("sourcecraft-secret", repr(catalog._settings))

    def test_factory_rejects_ambiguous_or_invalid_global_discovery(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            create_sourcecraft_public_repository_catalog_from_environment(
                {
                    "SOURCECRAFT_TOKEN": "secret",
                    "SOURCECRAFT_DISCOVER_PUBLIC_REPOSITORIES": "true",
                    "SOURCECRAFT_PUBLIC_ORGANIZATIONS": "team",
                }
            )
        with self.assertRaisesRegex(ValueError, "must be true or false"):
            create_sourcecraft_public_repository_catalog_from_environment(
                {"SOURCECRAFT_DISCOVER_PUBLIC_REPOSITORIES": "yes"}
            )


class CatalogHttpClient:
    def __init__(
        self,
        token: str,
        repositories_by_organization: dict[str, list[dict[str, object]]],
        *,
        discovered_repositories: list[dict[str, object]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.token = token
        self._repositories_by_organization = repositories_by_organization
        self._discovered_repositories = discovered_repositories
        self._error = error
        self.requests: list[str] = []
        self.request_params: list[dict[str, str | int] | None] = []
        self.closed = False

    def get_paginated_objects(
        self,
        path: str,
        *,
        items_field: str,
        params: dict[str, str | int] | None = None,
        page_size: int,
        max_pages: int,
    ) -> list[dict[str, object]]:
        self.requests.append(path)
        self.request_params.append(dict(params) if params is not None else None)
        if self._error is not None:
            raise self._error
        if items_field != "repositories" or page_size != 100 or max_pages != 100:
            raise AssertionError("unexpected SourceCraft catalog parameters")
        if path == "/repos":
            if params != {"sort_by": "created_at"} or self._discovered_repositories is None:
                raise AssertionError("unexpected SourceCraft discovery parameters")
            return self._discovered_repositories
        if params is not None:
            raise AssertionError("unexpected SourceCraft organization catalog parameters")
        return self._repositories_by_organization[
            path.removeprefix("/orgs/").removesuffix("/repos")
        ]

    def close(self) -> None:
        self.closed = True


def repository_payload(
    repository_id: str,
    slug: str,
    *,
    organization_slug: str = "alpha",
    visibility: str = "public",
) -> dict[str, object]:
    return {
        "id": repository_id,
        "name": slug,
        "slug": slug,
        "organization": {"slug": organization_slug},
        "visibility": visibility,
        "is_empty": False,
        "default_branch": "main",
        "language": {"name": "Python"},
        "counters": {"branches": "1"},
        "web_url": f"https://sourcecraft.dev/{organization_slug}/{slug}",
    }
