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


class SourceCraftPublicCatalogSettingsTest(unittest.TestCase):
    def test_factory_requires_complete_configuration_and_hides_token_from_repr(self) -> None:
        self.assertIsNone(create_sourcecraft_public_repository_catalog_from_environment({}))
        with self.assertRaisesRegex(ValueError, "configured together"):
            create_sourcecraft_public_repository_catalog_from_environment(
                {"SOURCECRAFT_TOKEN": "secret"}
            )
        with self.assertRaisesRegex(ValueError, "configured together"):
            create_sourcecraft_public_repository_catalog_from_environment(
                {"SOURCECRAFT_PUBLIC_ORGANIZATIONS": "team"}
            )

        settings = SourceCraftPublicCatalogSettings("sourcecraft-secret", ("team",))

        self.assertNotIn("sourcecraft-secret", repr(settings))


class CatalogHttpClient:
    def __init__(
        self,
        token: str,
        repositories_by_organization: dict[str, list[dict[str, object]]],
        *,
        error: Exception | None = None,
    ) -> None:
        self.token = token
        self._repositories_by_organization = repositories_by_organization
        self._error = error
        self.requests: list[str] = []
        self.closed = False

    def get_paginated_objects(
        self,
        path: str,
        *,
        items_field: str,
        page_size: int,
        max_pages: int,
    ) -> list[dict[str, object]]:
        self.requests.append(path)
        if self._error is not None:
            raise self._error
        if items_field != "repositories" or page_size != 100 or max_pages != 100:
            raise AssertionError("unexpected SourceCraft catalog parameters")
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
