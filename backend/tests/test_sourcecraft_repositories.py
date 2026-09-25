"""Проверяет каталог SourceCraft без сети и приватных репозиториев."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import Mock

import httpx

from backend.app.contracts import RepositoryRef
from backend.app.integrations.sourcecraft import (
    SourceCraftClient,
    SourceCraftRequestError,
    SourceCraftResponseError,
)
from backend.app.integrations.sourcecraft_repositories import (
    REPOSITORY_MAX_PAGES,
    REPOSITORY_PAGE_SIZE,
    SourceCraftRepositoryCatalogClient,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "sourcecraft" / "repositories_page.json"


class SourceCraftRepositoryCatalogClientTest(unittest.TestCase):
    def test_list_repositories_follows_pages_and_builds_safe_refs(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.params.get("page_token") == "page-2":
                return httpx.Response(
                    200,
                    json={
                        "repositories": [_repository_payload("repository-2", "second")],
                        "next_page_token": "",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "repositories": [_repository_payload("repository-1", "first")],
                    "next_page_token": "page-2",
                },
            )

        with httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            catalog = SourceCraftRepositoryCatalogClient(
                SourceCraftClient("test-token", http_client=http_client)
            )

            repositories = catalog.list_repositories("example-org")

        self.assertEqual([repository.slug for repository in repositories], ["first", "second"])
        self.assertEqual(
            repositories[0].as_repository_ref(),
            RepositoryRef(
                id="repository-1",
                organization_slug="example-org",
                repository_slug="first",
                web_url="https://sourcecraft.dev/example-org/first",
            ),
        )
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].url.path, "/orgs/example-org/repos")
        self.assertEqual(requests[0].url.params["page_size"], str(REPOSITORY_PAGE_SIZE))
        self.assertNotIn("page_token", requests[0].url.params)
        self.assertEqual(requests[1].url.params["page_token"], "page-2")

    def test_observed_fixture_maps_only_required_catalog_fields(self) -> None:
        payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        client = Mock(spec=SourceCraftClient)
        client.get_paginated_objects.return_value = payload["repositories"]
        catalog = SourceCraftRepositoryCatalogClient(client)

        repositories = catalog.list_repositories("example-organization")

        self.assertEqual(len(repositories), 1)
        repository = repositories[0]
        self.assertEqual(repository.id, "repository-id-redacted")
        self.assertEqual(repository.name, "example-private-repository")
        self.assertEqual(repository.default_branch, "main")
        self.assertEqual(repository.visibility, "private")
        self.assertFalse(repository.is_empty)
        self.assertEqual(repository.language, "Python")
        self.assertEqual(repository.branch_count, 2)
        client.get_paginated_objects.assert_called_once_with(
            "/orgs/example-organization/repos",
            items_field="repositories",
            page_size=REPOSITORY_PAGE_SIZE,
            max_pages=REPOSITORY_MAX_PAGES,
        )

    def test_empty_catalog_is_a_successful_empty_result(self) -> None:
        client = Mock(spec=SourceCraftClient)
        client.get_paginated_objects.return_value = []

        repositories = SourceCraftRepositoryCatalogClient(client).list_repositories(
            "example-org"
        )

        self.assertEqual(repositories, ())

    def test_empty_repository_may_have_no_branch_language_or_web_url(self) -> None:
        client = Mock(spec=SourceCraftClient)
        payload = _repository_payload("empty-id", "empty-repo")
        payload.update(
            {
                "default_branch": "",
                "is_empty": True,
                "language": None,
                "web_url": None,
                "counters": {"branches": "0"},
            }
        )
        client.get_paginated_objects.return_value = [payload]

        repository = SourceCraftRepositoryCatalogClient(client).list_repositories(
            "example-org"
        )[0]

        self.assertTrue(repository.is_empty)
        self.assertEqual(repository.default_branch, "")
        self.assertIsNone(repository.language)
        self.assertEqual(repository.branch_count, 0)
        self.assertIsNone(repository.as_repository_ref().web_url)

    def test_rejects_unsafe_organization_before_request(self) -> None:
        client = Mock(spec=SourceCraftClient)
        catalog = SourceCraftRepositoryCatalogClient(client)

        for organization_slug in ("", "../org", "org/name", "org?token=secret"):
            with self.subTest(organization_slug=organization_slug), self.assertRaises(
                SourceCraftRequestError
            ):
                catalog.list_repositories(organization_slug)

        client.get_paginated_objects.assert_not_called()

    def test_rejects_malformed_or_cross_organization_repositories(self) -> None:
        cases = (
            {"id": ""},
            {"name": ""},
            {"slug": "../private-marker"},
            {"visibility": "secret"},
            {"visibility": []},
            {"is_empty": "false"},
            {"default_branch": ""},
            {"language": []},
            {"language": {}},
            {"counters": {"branches": "not-a-number"}},
            {"counters": {"branches": str(2**64)}},
            {"organization": {"id": "org-id", "slug": "another-org"}},
            {"web_url": "https://attacker.example/private-marker"},
            {"web_url": "https://[private-marker"},
            {"web_url": "https://sourcecraft.dev/example-org/example-repo?private-marker"},
        )
        for update in cases:
            with self.subTest(update=update):
                client = Mock(spec=SourceCraftClient)
                payload = _repository_payload("repository-1", "example-repo")
                payload.update(update)
                client.get_paginated_objects.return_value = [payload]

                with self.assertRaises(SourceCraftResponseError) as raised:
                    SourceCraftRepositoryCatalogClient(client).list_repositories(
                        "example-org"
                    )

                self.assertNotIn("private-marker", str(raised.exception))

    def test_rejects_web_url_with_raw_control_characters(self) -> None:
        unsafe_urls = (
            "https://sourcecraft.dev/example-org/example-\nrepo",
            "https://sourcecraft.dev/example-org/example-\rrepo",
            "https://sourcecraft.dev/example-org/example-\trepo",
            "\x00https://sourcecraft.dev/example-org/example-repo",
            "https://sourcecraft.dev/example-org/example-repo\x7f",
        )
        for web_url in unsafe_urls:
            with self.subTest(web_url=repr(web_url)):
                client = Mock(spec=SourceCraftClient)
                payload = _repository_payload("repository-1", "example-repo")
                payload["web_url"] = web_url
                client.get_paginated_objects.return_value = [payload]

                with self.assertRaisesRegex(SourceCraftResponseError, "not an official URL"):
                    SourceCraftRepositoryCatalogClient(client).list_repositories(
                        "example-org"
                    )

    def test_rejects_duplicate_id_or_slug_across_pages(self) -> None:
        duplicate_cases = (
            (
                _repository_payload("same-id", "first"),
                _repository_payload("same-id", "second"),
            ),
            (
                _repository_payload("repository-1", "same-slug"),
                _repository_payload("repository-2", "same-slug"),
            ),
        )
        for payloads in duplicate_cases:
            with self.subTest(payloads=payloads):
                client = Mock(spec=SourceCraftClient)
                client.get_paginated_objects.return_value = list(payloads)

                with self.assertRaisesRegex(SourceCraftResponseError, "duplicate repository"):
                    SourceCraftRepositoryCatalogClient(client).list_repositories(
                        "example-org"
                    )

    def test_repr_does_not_expose_private_repository_metadata(self) -> None:
        client = Mock(spec=SourceCraftClient)
        client.get_paginated_objects.return_value = [
            _repository_payload("private-id", "private-slug", name="private-name")
        ]

        repository = SourceCraftRepositoryCatalogClient(client).list_repositories(
            "example-org"
        )[0]

        serialized = repr(repository)
        for private_value in (
            "private-id",
            "private-name",
            "example-org",
            "private-slug",
            "main",
            "Python",
            "sourcecraft.dev",
        ):
            with self.subTest(private_value=private_value):
                self.assertNotIn(private_value, serialized)


def _repository_payload(
    repository_id: str,
    slug: str,
    *,
    name: str | None = None,
) -> dict[str, object]:
    return {
        "id": repository_id,
        "name": name or slug,
        "default_branch": "main",
        "organization": {"id": "organization-id", "slug": "example-org"},
        "slug": slug,
        "is_empty": False,
        "visibility": "private",
        "web_url": f"https://sourcecraft.dev/example-org/{slug}",
        "counters": {"branches": "2"},
        "language": {"name": "Python", "color": "#3572A5"},
    }
